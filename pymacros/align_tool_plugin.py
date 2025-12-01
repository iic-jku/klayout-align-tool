# --------------------------------------------------------------------------------
# SPDX-FileCopyrightText: 2025 Martin Jan Köhler
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
# SPDX-License-Identifier: GPL-3.0-or-later
#--------------------------------------------------------------------------------


from dataclasses import dataclass
import os 
import sys
from typing import *

import pya

from klayout_plugin_utils.debugging import debug, Debugging
from klayout_plugin_utils.event_loop import EventLoop
from klayout_plugin_utils.str_enum_compat import StrEnum
from klayout_plugin_utils.tech_helpers import drc_tech_grid_um


class AlignToolState(StrEnum):
    INACTIVE = "inactive"
    PENDING_SELECTION1 = "pending_selection1"
    PENDING_SELECTION2 = "pending_selection2"


@dataclass
class AlignToolSelection:
    location: pya.Point
    search_box: pya.Box
    edge: Optional[pya.Edge]
    path: List[pya.InstElement]
    shape: Optional[pya.Shape]
    bbox_of_instance: Optional[pya.Instance]
    layer: Optional[int]
    snap_point: Optional[pya.Point]

    def __str__(self) -> str:
        return f"AlignToolSelection(location={self.location.to_s()}, "\
               f"search_box={self.search_box.to_s()}, "\
               f"edge={self.edge.to_s() if self.edge is not None else 'None'}, "\
               f"shape={self.shape.to_s() if self.shape is not None else 'None'}, "\
               f"layer={self.layer}"\
               f")"


class AlignToolSetupDock(pya.QDockWidget):
    def __init__(self):
        super().__init__()
        self.setupWidget = AlignToolSetupWidget()
        self.setWidget(self.setupWidget)
        self.setWindowTitle("Align Tool")
        
    def updateState(self, state: AlignToolState):
        self.setupWidget.updateState(state)

    def updatePreSelection(self, pre_selection: List[pya.Instance | pya.Shape]):
        self.setupWidget.updatePreSelection(pre_selection)
        
    def updateSelection1(self, selection: AlignToolSelection):
        self.setupWidget.updateSelection1(selection)

        
class AlignToolSetupWidget(pya.QWidget):
    def __init__(self):
        super().__init__()
        self.pre_selection_label = pya.QLabel('')
        self.selection1_value_label = pya.QLabel('<span style="text-decoration: underline;">Source ref:</span> None yet')
        self.selection1_status_label = pya.QLabel('')
        self.selection2_value_label = pya.QLabel('<span style="text-decoration: underline;">Target ref:</span> None yet')
        self.selection2_status_label = pya.QLabel('')
        self.spacerItem = pya.QSpacerItem(0, 20, pya.QSizePolicy.Minimum, pya.QSizePolicy.Fixed)
        self.cancelInfoLabel = pya.QLabel('<span style="color: grey;"><span style="text-decoration: underline;">Hint:</span> Esc to cancel</span>')
        
        self.layout = pya.QGridLayout()
        self.layout.setSpacing(10)
        self.layout.setVerticalSpacing(5)
        self.layout.addWidget(self.pre_selection_label,     0, 0)
        self.layout.addWidget(self.selection1_value_label,  1, 0)
        self.layout.addWidget(self.selection1_status_label, 1, 1)
        self.layout.addWidget(self.selection2_value_label,  2, 0)
        self.layout.addWidget(self.selection2_status_label, 2, 1)
        self.layout.addItem(self.spacerItem)
        self.layout.addWidget(self.cancelInfoLabel,         4, 0)
        self.layout.setRowStretch(5, 1)
        self.setLayout(self.layout)
         
    def hideEvent(self, event):
        event.accept()
    
    def updateState(self, state: AlignToolState):
        msg: str = ""
        match state:
            case AlignToolState.INACTIVE:
                self.selection1_status_label.setText("")
                self.selection2_status_label.setText("")
            case AlignToolState.PENDING_SELECTION1:
                self.selection1_status_label.setText(
                    '<span style="color:blue; font-weight:bold;">⬅</span> '
                    '<span style="font-weight:bold; color:blue;">Next</span>'
                )
                self.selection2_status_label.setText("")
            case AlignToolState.PENDING_SELECTION2:
                self.selection1_status_label.setText("✅")
                self.selection2_status_label.setText(
                    '<span style="color:blue; font-weight:bold;">⬅</span> '
                    '<span style="font-weight:bold; color:blue;">Next</span>'
                )

    def updatePreSelection(self, pre_selection: List[pya.Instance | pya.Shape]):
        if len(pre_selection) == 0:
            msg = '<span style="text-decoration: underline;">Pre-selection:</span> None'
        else:
            def format_len(l: List, singular: str) -> Optional[str]:
                n = len(l)
                match n:
                    case 0: return None
                    case 1: return f"1 {singular}"
                    case _: return f"{n} {singular}s"
            
            instances = [o for o in pre_selection if isinstance(o, pya.Instance)]
            shapes = [o for o in pre_selection if isinstance(o, pya.Shape)]
            entries = [
                format_len(instances, "instance"),
                format_len(shapes, "shape")
            ]
            msg = '<span style="text-decoration: underline;">Pre-selection:</span> ' + \
                  ', '.join([e for e in entries if e is not None])
        self.pre_selection_label.setText(msg)

    def format_selection(self, selection: Optional[AlignToolSelection]) -> str:
        if selection is None:
            return "None yet"
        if selection.snap_point is not None:
            return "1 point"
        return "1 edge"

    def updateSelection1(self, selection: Optional[AlignToolSelection]):
        txt = '<span style="text-decoration: underline;">Source ref:</span> ' + \
              self.format_selection(selection)
        self.selection1_value_label.setText(txt)


class AlignToolPlugin(pya.Plugin):
    def __init__(self, view: pya.LayoutView):
        super().__init__()
        self.setupDock      = None
        self.view            = view

        self._state = AlignToolState.INACTIVE
        self._pre_selected_objects = []
        self._selection1: Optional[AlignToolSelection] = None
        self.markers_selection1 = []
        self.markers_selection2 = []
        self.preview_selection1: Optional[AlignToolSelection] = None
        self.preview_selection2: Optional[AlignToolSelection] = None
        self.toolTip = pya.QToolTip()

    @property
    def cell_view(self) -> pya.CellView:
        return self.view.active_cellview()

    @property
    def layout(self) -> pya.Layout:
        return self.cell_view.layout()
        
    @property
    def dbu(self) -> float:
        return self.layout.dbu

    @property
    def state(self) -> AlignToolState:
        return self._state

    @state.setter
    def state(self, state: AlignToolState):
        if Debugging.DEBUG:
            debug(f"Transitioning from {self._state.value} to {state.value}")
        self._state = state
        if not(self.setupDock):
            pass
        else:
            self.setupDock.updateState(state)

    @property
    def pre_selected_objects(self) -> List[pya.Instance | pya.Shape]:
        return self._pre_selected_objects

    @pre_selected_objects.setter
    def pre_selected_objects(self, objects: List[pya.Instance | pya.Shape]):
        if Debugging.DEBUG:
            debug(f"Setting pre_selected_objects ({len(self.pre_selected_objects)}): {self.pre_selected_objects}")
        
        self._pre_selected_objects = objects
        if not(self.setupDock):
            pass
        else:
            self.setupDock.updatePreSelection(objects)

    @property
    def selection1(self) -> AlignToolSelection:
        return self._selection1

    @selection1.setter
    def selection1(self, selection: AlignToolSelection):
        if Debugging.DEBUG:
            debug(f"setting selection1 to {selection}")
        self._selection1 = selection
        if not(self.setupDock):
            pass
        else:
            self.setupDock.updateSelection1(selection)

    @property
    def search_box_marker_visible(self) -> bool:
        return True  # DEBUG

    def selected_objects(self) -> List:
        l = []
        for o in self.view.each_object_selected():
            if o.is_cell_inst():
                l += [o.inst()]
            elif o.shape is not None:
                l += [o.shape]
        return l
        
    def show_editor_options(self):
        mw = pya.Application.instance().main_window()
    
        # NOTE: if we directly call the Editor Options menu action
        #       the GUI immediately will switch back to the Librariew view
        #       so we enqueue it into the event loop
        EventLoop.defer(lambda w=mw: w.call_menu('cm_edit_options'))
               
    def activated(self):
        view_is_visible = self.view.widget().isVisible()
        if Debugging.DEBUG:
            debug(f"AlignToolPlugin.activated, "
                  f"for cell view {self.cell_view.cell_name}, "
                  f"is visible: {view_is_visible}")
            debug(f"viewport trans: {self.view.viewport_trans()}")
        if not view_is_visible:
            return

        if not(self.setupDock):
            mw   = pya.Application.instance().main_window()
            self.setupDock = AlignToolSetupDock()
            mw.addDockWidget(pya.Qt_DockWidgetArea.RightDockWidgetArea, self.setupDock)
        self.setupDock.show()

        self.show_editor_options()

        self.pre_selected_objects = self.selected_objects()
        
        self.state = AlignToolState.PENDING_SELECTION1
            
    def deactivated(self):
        if Debugging.DEBUG:
            debug("AlignToolPlugin.deactivated")
        
        self.state = AlignToolState.INACTIVE
        self.pre_selected_objects = []
        
        self._clear_all_markers()
        self.ungrab_mouse()
        if self.setupDock:
            self.setupDock.hide()

    def deactivate(self):
        if Debugging.DEBUG:
            debug("AlignToolPlugin.deactive")
        esc_key  = 16777216 
        keyPress = pya.QKeyEvent(pya.QKeyEvent.KeyPress, esc_key, pya.Qt.NoModifier)
        pya.QApplication.sendEvent(self.view.widget(), keyPress)        

    def _clear_all_markers(self):
        self._clear_markers_selection1()
        self._clear_markers_selection2()
        
    def _clear_markers_selection1(self):
        for marker in self.markers_selection1:
            marker._destroy()
        self.markers_selection1 = []
        self.preview_selection1 = None

    def _clear_markers_selection2(self):
        for marker in self.markers_selection2:
            marker._destroy()
        self.markers_selection2 = []
        self.preview_selection2 = None

    def find_nearest_edge_point(self, location: pya.Point, edge: pya.Edge) -> pya.Point:
        """
        On the chosen edge, we want to find nearest end point or the center point
        """

        def halfway(a: int, b:int) -> int:
            if a < b:
                return a + (b - a) / 2
            else:
                return b + (a - b) / 2

        nearest_point = None
        nearest_distance = 9999999999
        
        edge_center = pya.Point(halfway(edge.x1, edge.x2), halfway(edge.y1, edge.y2))
        points = [edge_center, edge.p1, edge.p2]
        distances = [abs(p.distance(location)) for p in points]
        
        for point, distance in zip(points, distances):
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_point = point  
        
        return nearest_point
    
    def visible_layer_indexes(self) -> List[int]:
        idxs = []
        for lref in self.view.each_layer():
            if lref.visible and lref.valid:
                if lref.layer_index() == -1:  # hidden by the user
                    continue
                # print(f"layer is visible, name={lref.name}, idx={lref.layer_index()}, "
                #       f"marked={lref.marked} cellview={lref.cellview()}, "
                #      f"source={lref.source}")
                idxs.append(lref.layer_index())
        return idxs
    
    def find_selection(self, location: pya.DPoint, max_distance: int, consider_rulers: bool) -> Optional[AlignToolSelection]:
        location = location.to_itype(self.dbu)
                
        search_box = pya.Box(location.x - max_distance, location.y - max_distance, 
                             location.x + max_distance, location.y + max_distance)

        nearest = AlignToolSelection(location=location,
                                     search_box=search_box,
                                     edge=None,
                                     path=[],    
                                     shape=None,
                                     bbox_of_instance=None,
                                     layer=None,
                                     snap_point=None)
        
        def consider_edge_selection(selection: AlignToolSelection):
            nonlocal nearest, search_box, location
            intersection = selection.edge.clipped(search_box)
            if intersection is None:
                return
            elif nearest.edge is None:
                nearest = selection
            else:
                old_intersection = nearest.edge.clipped(search_box)
                dist_old = old_intersection.distance_abs(location)
                dist_new = intersection.distance_abs(location)
                if dist_new < dist_old:
                    nearest = selection
                # # Hotspot, don't log this
                # else:
                #    if Debugging.DEBUG:                
                #        debug(f"dist_old({dist_old}) >= dist_new({dist_new}), old edge {nearest.edge} not replaced by {selection.edge}, "
                #              f"intersections old {old_intersection} vs new {intersection}")
        
        visible_layer_indexes = self.visible_layer_indexes()
        
        # NOTE: self.layout.top_cells() are the top cells from the layout perspective,
        #       but self.cell_view.cell is the current top cell,  
        #       which is user-configurable via 'Show As New Top'
        
        top_cell = self.cell_view.cell
        if self.cell_view.is_cell_hidden(top_cell):
            return None
        
        iteration_limit = 1000
        
        # we prioritize the child instances of top cell
        # for those we also consider the bounding box
        if self.view.max_hier_levels >= 1:
            iter = top_cell.begin_instances_rec_overlapping(search_box)
            iter.min_depth = max(self.view.min_hier_levels-1, 0)
            iter.max_depth = max(self.view.max_hier_levels-1, 0)
            i = 0
            while not iter.at_end():
                inst = iter.current_inst_element().inst()
                hidden = self.view.is_cell_hidden(inst.cell.cell_index(), self.view.active_cellview_index)
                # # Hotspot, don't log this
                # if Debugging.DEBUG:                
                #     debug(f"inst from cell {inst.cell.name} hidden? {hidden}, "
                #           f"trans={iter.trans() * iter.inst_trans()}, "
                #           f"inst_bbox={inst.bbox()}")
                if not hidden:
                    inst_bbox_from_top = inst.bbox().transformed(iter.trans())
                    edges = pya.Edges(inst_bbox_from_top)
                    for e in edges:
                        if e.clipped(search_box) is not None:
                            consider_edge_selection(
                                AlignToolSelection(location=location,
                                                   search_box=search_box,
                                                   edge=e,
                                                   path=iter.path(),
                                                   shape=inst_bbox_from_top,
                                                   bbox_of_instance=inst,
                                                   layer=None,
                                                   snap_point=None)  # NOTE: snap point will be set later
                            )
                    midpoint = inst_bbox_from_top.center()
                    midpoint_dummy_edge = pya.Edge(midpoint, midpoint)
                    consider_edge_selection(
                        AlignToolSelection(location=location,
                                           search_box=search_box,
                                           edge=midpoint_dummy_edge,
                                           path=iter.path(),
                                           shape=inst_bbox_from_top,
                                           bbox_of_instance=inst,
                                           layer=None,
                                           snap_point=midpoint)
                    )
                iter.next()
                i += 1
                if i >= iteration_limit:
                    break
        
        # for lyr, li in enumerate(self.layout.layer_infos()):
        ## NOTE: GUI levels 0 .. 0 means that only the TOP cell(s) are viewed from outside!
        if self.view.max_hier_levels >= 1:
            for lyr in visible_layer_indexes:
                iter = top_cell.begin_shapes_rec_overlapping(lyr, search_box)
                iter.min_depth = max(self.view.min_hier_levels-1, 0)
                iter.max_depth = max(self.view.max_hier_levels-1, 0)
                i = 0
                while not iter.at_end():
                    sh = iter.shape()
                    # # Hotspot, don't log this
                    # if Debugging.DEBUG:
                    #     debug(f"lyr {lyr} ({li}), found {sh}")
                    pg = sh.polygon
                    if pg is None:
                        # # Hotspot, don't log this
                        # if Debugging.DEBUG:
                        #     debug(f"Skip shape {sh}, it's has no polygon")
                        pass
                    else:
                        p = sh.polygon.transformed(iter.itrans())
                        for e in p.each_edge():
                            consider_edge_selection(
                                AlignToolSelection(location=location,
                                                   search_box=search_box,
                                                   edge=e,
                                                   path=iter.path(),
                                                   shape=sh,
                                                   bbox_of_instance=None,
                                                   layer=lyr,
                                                   snap_point=None)  # NOTE: snap point will be set later
                            )
                        midpoint = p.bbox().center()
                        midpoint_dummy_edge = pya.Edge(midpoint, midpoint)
                        consider_edge_selection(
                            AlignToolSelection(location=location,
                                               search_box=search_box,
                                               edge=midpoint_dummy_edge,
                                               path=iter.path(),
                                               shape=sh,
                                               bbox_of_instance=None,
                                               layer=None,
                                               snap_point=midpoint)
                        )
                    
                    iter.next()
                    i += 1
                    if i >= iteration_limit:
                        break

        if consider_rulers:
            for a in self.view.each_annotation():
                points: List[pya.Point] = []
                edges: List[pya.Edge] = []
                match a.outline:
                    case pya.Annotation.OutlineBox:
                        box = a.box().to_itype(self.dbu)
                        edges = pya.Polygon(box).each_edge()
                    case _:
                        points = [dp.to_itype(self.dbu) for dp in a.points]
                for e in edges:
                    clipped_edge = e.clipped(search_box)
                    if clipped_edge:
                        consider_edge_selection(
                            AlignToolSelection(location=location,
                                               search_box=search_box,
                                               edge=e,
                                               path=[],
                                               shape=None,
                                               bbox_of_instance=None,
                                               layer=None,
                                               snap_point=None)  # NOTE: snap point will be set later
                        )
                for p in points:
                    if search_box.contains(p):
                        e = pya.Edge(p, p)
                        consider_edge_selection(
                            AlignToolSelection(location=location,
                                               search_box=search_box,
                                               edge=e,
                                               path=[],
                                               shape=None,
                                               bbox_of_instance=None,
                                               layer=None,
                                               snap_point=p)
                        )

        if nearest.edge is None:
            return None
        
        nearest_point = self.find_nearest_edge_point(location=location, edge=nearest.edge)
        if search_box.contains(nearest_point):
            nearest.snap_point = nearest_point
        return nearest
    
    def viewport_adjust(self, v: int) -> int:
        trans = pya.CplxTrans(self.view.viewport_trans(), self.dbu)
        return v / trans.mag

    @property
    def max_distance(self) -> int:
        return self.viewport_adjust(20)
    
    def preview_markers_for_selection(self, selection: AlignToolSelection) -> List[pya.Marker]:
        markers = []
    
        edge_marker = pya.Marker(self.view)
        edge_marker.line_style     = 0
        edge_marker.line_width     = 2
        edge_marker.vertex_size    = 0 
        edge_marker.dither_pattern = 2
        edge_marker.set(selection.edge.to_dtype(self.dbu))
        markers += [edge_marker]
        
        if selection.snap_point is not None:
            point_marker = pya.Marker(self.view)
            point_marker.line_style     = 1
            point_marker.line_width     = 2
            point_marker.vertex_size    = 0
            point_marker.dither_pattern = 0
            d = self.viewport_adjust(5)
            marker_box = pya.Box(pya.Point(selection.snap_point.x - d, selection.snap_point.y - d), 
                                 pya.Point(selection.snap_point.x + d, selection.snap_point.y + d))
            point_marker.set(marker_box.to_dtype(self.dbu))
            markers += [point_marker]
        
        if self.search_box_marker_visible:
            search_box_marker = pya.Marker(self.view)
            search_box_marker.line_style = 2
            search_box_marker.line_width = 1
            search_box_marker.vertex_size = 0
            search_box_marker.dither_pattern = -1
            search_box_marker.set(selection.search_box.to_dtype(self.dbu))
            markers += [search_box_marker]

        return markers
    
    def mouse_moved_event(self, dpoint: pya.DPoint, buttons: int, prio: bool):
        if prio:
            # # Hotspot, don't log this       
            # if Debugging.DEBUG:
            #     debug(f"mouse moved event, p={dpoint}, prio={prio}")
            
            # only consider rulers to specify a destination
            consider_rulers = self.state == AlignToolState.PENDING_SELECTION2
            
            selection = self.find_selection(location=dpoint, 
                                            max_distance=self.max_distance,
                                            consider_rulers=consider_rulers)
            if selection is None:
                match self.state:
                    case AlignToolState.INACTIVE:
                        return False
                    case AlignToolState.PENDING_SELECTION1:
                        self.toolTip.showText(pya.QCursor.pos, "Select shape feature to align") 
                    case AlignToolState.PENDING_SELECTION2:
                        self.toolTip.showText(pya.QCursor.pos, "Select shape feature to reference") 
                return False  
            
            match self.state:
                case AlignToolState.INACTIVE:
                    return False
                 
                case AlignToolState.PENDING_SELECTION1:
                    self._clear_markers_selection1()
                    self.markers_selection1 = self.preview_markers_for_selection(selection)
                    self.preview_selection1 = selection

                case AlignToolState.PENDING_SELECTION2:
                    self._clear_markers_selection2()
                    self.markers_selection2 = self.preview_markers_for_selection(selection)
                    self.preview_selection2 = selection

            return True           
        return False
        
    def mouse_click_event(self, dpoint: pya.DPoint, buttons: int, prio: bool):
        if prio:
            if buttons in [8]:  # Left click
                match self.state:
                    case AlignToolState.INACTIVE:
                        return False
                     
                    case AlignToolState.PENDING_SELECTION1:
                        if self.preview_selection1 is None:
                            return False
                        self.selection1 = self.preview_selection1
                        self.state = AlignToolState.PENDING_SELECTION2
                        
                    case AlignToolState.PENDING_SELECTION2:
                        if self.preview_selection2 is None:
                            return False
                    
                        selection1 = self.selection1
                        selection2 = self.preview_selection2
                        
                        self.state = AlignToolState.INACTIVE
                        self.selection1 = None
                        
                        self.commit_align(selection1, selection2)
                
            if buttons in [16, 32]:
                self._clear_all_markers()
                self.state = AlignToolState.PENDING_SELECTION1
                self.selection1 = None

            return True
        return False
        
    def commit_align(self, selection1: AlignToolSelection, selection2: AlignToolSelection):
        # NOTE:
        #      Point1 -> Point2: X/Z movement
        #      Point1 -> Edge2: TODO???
        #      Edge1 -> Point2: TODO???
        #      Edge1 -> Edge2: move only in direction normal to the edge
        
        is_point1 = selection1.snap_point is not None
        is_edge1 = not is_point1
        is_point2 = selection2.snap_point is not None
        is_edge2 = not is_point2

        dx = 0
        dy = 0
        transformees = []

        if len(self.pre_selected_objects) >= 1:  # user had a preselection of one or multiple objects
            transformees = self.pre_selected_objects
        elif len(selection1.path) == 0:  # a shape within the same cell has to be aligned
            inst1 = selection1.bbox_of_instance
            if inst1 is None:
                transformees = [selection1.shape]
            else:  # bounding box of an instance
                transformees = [inst1]
        elif len(selection1.path) >= 1:  # an instance has to be aligned
            transformees = [selection1.path[0].inst()]

        if is_point1 and is_point2:
            dx = selection2.snap_point.x - selection1.snap_point.x
            dy = selection2.snap_point.y - selection1.snap_point.y
        elif is_point1 and is_edge2:
            edge2 = selection2.edge
            is_horizontal = edge2.dy() == 0
            is_vertical = edge2.dx() == 0
            if is_horizontal:
                dx = 0
                dy = edge2.p1.y - selection1.snap_point.y
            elif is_vertical:
                dx = edge2.p1.x - selection1.snap_point.x
                dy = 0
        elif is_edge1 and is_point2:
            edge1 = selection1.edge
            is_horizontal = edge1.dy() == 0
            is_vertical = edge1.dx() == 0
            if is_horizontal:
                dx = 0
                dy = selection2.snap_point.y - edge1.p1.y
            elif is_vertical:
                dx = selection2.snap_point.x - edge1.p1.x
                dy = 0
        elif is_edge1 and is_edge2:
            edge1 = selection1.edge
            edge2 = selection2.edge
            if edge1.is_parallel(edge2):
                is_horizontal = edge1.dy() == 0
                is_vertical = edge1.dx() == 0
                if is_horizontal:
                    dx = 0
                    dy = edge2.p1.y - edge1.p1.y
                elif is_vertical:
                    dx = edge2.p1.x - edge1.p1.x
                    dy = 0
            else:
                pya.QMessageBox.warning(self.view.widget(), "Unsupported use case", 
                                        "Aligning non-parallel edges is not yet supported.")
                self.deactivate()
                return


        # snap to technical minimum grid (avoid DRC offgrid errors)
        grid_dbu = int(drc_tech_grid_um() / self.dbu)        
        snapped_dx = round(dx / grid_dbu) * grid_dbu
        snapped_dy = round(dy / grid_dbu) * grid_dbu
        
        self.view.transaction("align")
        try:
            trans = pya.Trans(snapped_dx, snapped_dy)
            for t in transformees:
                t.transform(trans)
        finally:
            self.view.commit()
            self.deactivate()


class AlignToolPluginFactory(pya.PluginFactory):
    def __init__(self):
        super().__init__()
        self.register(-1000, "Align Tool", "Align (A)", ':align_hcenter_32px')
  
    def create_plugin(self, manager, root, view):
        return AlignToolPlugin(view)

