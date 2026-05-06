# -*- coding: utf-8 -*-
"""Marker shape QComboBox: QGIS marker previews in a wrapping grid (custom popup — no Qt list scrollbar)."""

import inspect
import os

from qgis.PyQt.QtCore import QEvent, QMetaObject, QPoint, QRect, QSize, Qt, pyqtSlot
from qgis.PyQt.QtGui import QColor, QIcon, QPainter, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QScrollArea,
    QStyle,
    QStyleFactory,
    QStyleOptionComboBox,
    QToolButton,
    QVBoxLayout,
)

from qgis.core import (
    QgsMarkerSymbol,
    QgsSimpleMarkerSymbolLayer,
    QgsSymbolLayerUtils,
    QgsUnitTypes,
    Qgis,
)

# Qgis.MarkerShape exists from newer QGIS (~3.30+); QGIS 3.6 uses QgsSimpleMarkerSymbolLayer.Shape + ints.
_HAVE_QGIS_MARKER_SHAPE = hasattr(Qgis, "MarkerShape")

_QSS_SPLIT_MARKER = "/* MW_MARKER_SHAPE_SPLIT_POPUP */"
_QSS_GRID_MARKER = "/* MW_MARKER_GRID_POPUP_ONLY */"
_qss_combo_ss_cache = None

_GRID_COLUMNS = 3
_GRID_SPACING = 4
_ICON_PIX = 34
_POPUP_MIN_WIDTH_EXTRA = 28
_POPUP_LIST_HEIGHT_FRACTION = 0.5

# int id → native value from QgsSimpleMarkerSymbolLayer.availableShapes() (required for setShape on QGIS < ~3.30)
_LEGACY_NATIVE_BY_INT = {}
# encoded name → native from availableShapes (decodeShape can be finicky across sip builds)
_LEGACY_NATIVE_BY_ENCODED = {}


def _application_screen_at_global(pos):
    """Qt 5.10+ QApplication.screenAt; older Qt / QGIS 3.6 fallbacks."""
    app = QApplication.instance()
    if app is None:
        return None
    fn = getattr(app, "screenAt", None)
    if callable(fn):
        try:
            return fn(pos)
        except Exception:
            return None
    pfn = getattr(app, "primaryScreen", None)
    return pfn() if callable(pfn) else None


def _register_native_marker_shape(value):
    try:
        _LEGACY_NATIVE_BY_INT[int(value)] = value
    except Exception:
        pass
    try:
        k = QgsSimpleMarkerSymbolLayer.encodeShape(value)
        if k:
            _LEGACY_NATIVE_BY_ENCODED[str(k)] = value
    except Exception:
        pass


def _decode_shape_string(name):
    """QgsSimpleMarkerSymbolLayer.decodeShape wrapper (PyQt5 / QGIS 3.6+)."""
    if not isinstance(name, str) or not name:
        return None
    if name in _LEGACY_NATIVE_BY_ENCODED:
        return _LEGACY_NATIVE_BY_ENCODED[name]
    fn = getattr(QgsSimpleMarkerSymbolLayer, "decodeShape", None)
    if not callable(fn):
        return None
    for args in ((name,), (name, None)):
        try:
            r = fn(*args)
        except Exception:
            continue
        if isinstance(r, tuple) and r:
            return r[0]
        if r is not None:
            return r
    return None


def _legacy_shape_value_from_int(iv):
    """QgsSimpleMarkerSymbolLayer.setShape / encodeShape argument for legacy QGIS (not Qgis.MarkerShape)."""
    iv = int(iv)
    if iv in _LEGACY_NATIVE_BY_INT:
        return _LEGACY_NATIVE_BY_INT[iv]
    for cls in (QgsSimpleMarkerSymbolLayer,):
        for name in dir(cls):
            if not name.startswith("Shape"):
                continue
            ev = getattr(cls, name, None)
            if ev is None:
                continue
            try:
                if int(ev) == iv:
                    _LEGACY_NATIVE_BY_INT[iv] = ev
                    return ev
            except Exception:
                continue
    try:
        from qgis.core import QgsSimpleMarkerSymbolLayerBase as _B

        for name in dir(_B):
            if not name.startswith("Shape"):
                continue
            ev = getattr(_B, name, None)
            if ev is None:
                continue
            try:
                if int(ev) == iv:
                    _LEGACY_NATIVE_BY_INT[iv] = ev
                    return ev
            except Exception:
                continue
    except ImportError:
        pass
    return iv


def _load_marker_qss_parts():
    """Return (merged_combo_stylesheet, grid_popup_stylesheet)."""
    global _qss_combo_ss_cache
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "marker_shape_combo.qss")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    if _qss_combo_ss_cache is not None and _qss_combo_ss_cache[0] == mtime:
        return _qss_combo_ss_cache[1], _qss_combo_ss_cache[2]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if _QSS_SPLIT_MARKER not in text:
        raise ValueError("marker_shape_combo.qss must contain split marker %r" % (_QSS_SPLIT_MARKER,))
    grid_qss = ""
    if _QSS_GRID_MARKER in text:
        text, grid_tail = text.split(_QSS_GRID_MARKER, 1)
        grid_qss = grid_tail.strip()
    closed, popup_rest = text.split(_QSS_SPLIT_MARKER, 1)
    merged = closed.strip() + "\n" + popup_rest.strip()
    _qss_combo_ss_cache = (mtime, merged, grid_qss)
    return merged, grid_qss


def _qss_marker_combo_stylesheet():
    merged, _grid = _load_marker_qss_parts()
    return merged


def _qss_grid_popup_stylesheet():
    _merged, grid = _load_marker_qss_parts()
    return grid


def coerce_marker_shape_for_setshape(shape_like):
    """Value suitable for QgsSimpleMarkerSymbolLayer.setShape() / encodeShape on QGIS 3.x."""
    if shape_like is None:
        return None
    if isinstance(shape_like, str):
        d = _decode_shape_string(shape_like)
        return d if d is not None else shape_like
    try:
        iv = int(shape_like)
    except (TypeError, ValueError):
        return shape_like
    if _HAVE_QGIS_MARKER_SHAPE:
        try:
            return Qgis.MarkerShape(iv)
        except Exception:
            pass
    return _legacy_shape_value_from_int(iv)


def _native_dedupe_key(native):
    """QGIS 3.6.1: int(sip enum) can collapse; encodeShape can return duplicates — fall back to object id."""
    try:
        s = str(QgsSimpleMarkerSymbolLayer.encodeShape(native))
        if s:
            return ("e", s)
    except Exception:
        pass
    return ("id", id(native))


def _natives_semantically_equal(a, b):
    if a is b:
        return True
    try:
        sa = str(QgsSimpleMarkerSymbolLayer.encodeShape(a))
        sb = str(QgsSimpleMarkerSymbolLayer.encodeShape(b))
        if sa and sb and sa == sb:
            return True
    except Exception:
        pass
    try:
        return int(a) == int(b)
    except Exception:
        return False


def _available_shapes_raw():
    raw = []
    Base = None
    try:
        from qgis.core import QgsSimpleMarkerSymbolLayerBase as Base
    except ImportError:
        pass
    for cls in (Base, QgsSimpleMarkerSymbolLayer):
        if cls is None:
            continue
        fn = getattr(cls, "availableShapes", None)
        if not callable(fn):
            continue
        try:
            lst = list(fn())
            if lst:
                return lst
        except Exception:
            continue
    return raw


def _fallback_native_list():
    """All built-in simple-marker shapes from enum members (always union with availableShapes on old QGIS)."""
    out = []
    if _HAVE_QGIS_MARKER_SHAPE:
        try:
            for x in Qgis.MarkerShape:
                out.append(x)
            if out:
                return out
        except Exception:
            pass
    classes = [QgsSimpleMarkerSymbolLayer]
    try:
        from qgis.core import QgsSimpleMarkerSymbolLayerBase as _B

        classes.insert(0, _B)
    except ImportError:
        pass
    seen_key = set()
    for cls in classes:
        for name in dir(cls):
            if not name.startswith("Shape"):
                continue
            if name.startswith("ShapeIs") or name in ("Shape", "Shapes"):
                continue
            ev = getattr(cls, name, None)
            if ev is None or inspect.isroutine(ev):
                continue
            k = _native_dedupe_key(ev)
            if k in seen_key:
                continue
            seen_key.add(k)
            out.append(ev)
    return out


def _preferred_native_order():
    if _HAVE_QGIS_MARKER_SHAPE:
        pref = []
        for attr in ("Circle", "Square", "Diamond"):
            v = getattr(Qgis.MarkerShape, attr, None)
            if v is not None:
                pref.append(v)
        if pref:
            return pref
    pref = []
    for name in ("ShapeCircle", "ShapeSquare", "ShapeDiamond"):
        v = getattr(QgsSimpleMarkerSymbolLayer, name, None)
        if v is not None:
            pref.append(v)
    return pref


def _encode_sort_key(n):
    try:
        return str(QgsSimpleMarkerSymbolLayer.encodeShape(n))
    except Exception:
        return ""


def _reorder_natives_preferred_first(natives):
    if not natives:
        return []
    rest = list(natives)
    head = []
    for p in _preferred_native_order():
        for i, n in enumerate(rest):
            if _natives_semantically_equal(p, n):
                head.append(n)
                rest.pop(i)
                break
    rest.sort(key=lambda n: (_encode_sort_key(n), str(type(n)), id(n)))
    return head + rest


def _ordered_natives():
    """Merge API list + static enums; dedupe without int-only keys (fixes single-square list on QGIS 3.6.1)."""
    seen_key = set()
    combined = []
    for native in _available_shapes_raw() + _fallback_native_list():
        if native is None:
            continue
        _register_native_marker_shape(native)
        k = _native_dedupe_key(native)
        if k in seen_key:
            continue
        seen_key.add(k)
        combined.append(native)
    ordered = _reorder_natives_preferred_first(combined)
    out = ordered if ordered else combined
    if not out:
        for name in ("ShapeCircle", "ShapeSquare", "ShapeDiamond"):
            v = getattr(QgsSimpleMarkerSymbolLayer, name, None)
            if v is not None:
                return [v]
    return out


def _find_native_index(natives, marker):
    if marker is None or not natives:
        return -1
    for i, n in enumerate(natives):
        if _natives_semantically_equal(marker, n):
            return i
    return -1


def _shape_preview_icon_native(native, px):
    if native is None:
        return QIcon()
    try:
        sl = QgsSimpleMarkerSymbolLayer()
        sl.setShape(native)
        sl.setSize(4.5)
        sl.setSizeUnit(QgsUnitTypes.RenderMillimeters)
        sl.setColor(QColor(220, 220, 220))
        sl.setStrokeColor(QColor(35, 35, 35))
        sl.setStrokeStyle(Qt.SolidLine)
        sl.setStrokeWidth(0.25)
        sl.setStrokeWidthUnit(QgsUnitTypes.RenderMillimeters)
        sym = QgsMarkerSymbol()
        sym.appendSymbolLayer(sl)
        pm = QgsSymbolLayerUtils.symbolPreviewPixmap(sym, QSize(max(8, px), max(8, px)), 2)
        if pm is not None and not pm.isNull():
            return QIcon(pm)
    except Exception:
        pass
    return QIcon()


def _placeholder_icon(px):
    pm = QPixmap(max(8, px), max(8, px))
    pm.fill(QColor(240, 240, 240))
    p = QPainter(pm)
    p.setPen(QColor(60, 60, 60))
    p.drawRect(2, 2, pm.width() - 4, pm.height() - 4)
    p.end()
    return QIcon(pm)


class _MarkerShapeGridPopup(QFrame):
    """Fusion + QScrollArea + tool buttons — avoids QComboBox QListView / native line-step scrollers."""

    def __init__(self, combo):
        super(_MarkerShapeGridPopup, self).__init__(combo)
        self.setObjectName("mwMarkerShapePickerPopup")
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self._combo = combo
        self._app_filter_installed = False
        fusion = QStyleFactory.create("Fusion")
        if fusion is not None:
            self.setStyle(fusion)
        gqss = _qss_grid_popup_stylesheet()
        if gqss:
            self.setStyleSheet(gqss)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("mwMarkerShapeScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._inner = QFrame(self._scroll)
        self._inner.setObjectName("mwMarkerShapePickerInner")
        self._grid = QGridLayout(self._inner)
        self._grid.setSpacing(_GRID_SPACING)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._scroll.setWidget(self._inner)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._scroll)

        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)
        self._buttons = []

    def hide_for_combo(self):
        if self._app_filter_installed:
            try:
                QApplication.instance().removeEventFilter(self)
            except Exception:
                pass
            self._app_filter_installed = False
        self.hide()

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is QApplication.instance() and event.type() == QEvent.MouseButtonPress:
            if self.isVisible():
                gp = event.globalPos()
                fg = self.frameGeometry()
                if fg.contains(gp):
                    return False
                cg = QRect(self._combo.mapToGlobal(QPoint(0, 0)), self._combo.size())
                if cg.contains(gp):
                    self.hide_for_combo()
                    return False
                self.hide_for_combo()
            return False
        return False

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self.hide_for_combo()
            return
        super(_MarkerShapeGridPopup, self).keyPressEvent(event)

    def _clear_grid(self):
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._buttons = []
        for bid in self._btn_group.buttons():
            self._btn_group.removeButton(bid)

    def _pick_index(self, idx):
        c = self._combo
        c.setCurrentIndex(idx)
        # Custom grid bypasses the native list; PyQt5 overloaded activated often rejects .emit(idx).
        # Plugin also listens to currentIndexChanged so the symbol updates on all QGIS 3.x builds.
        sig = getattr(c, "activated", None)
        if sig is not None:
            for attempt in (
                lambda: sig.emit(int(idx)),
                lambda: sig[int].emit(int(idx)),
            ):
                try:
                    attempt()
                    break
                except (TypeError, AttributeError):
                    continue
        self.hide_for_combo()

    def sync_and_show(self):
        c = self._combo
        self._clear_grid()
        col_w, row_h = c._mw_cell_metrics()
        px = c.iconSize().height()
        isz = QSize(px, px)
        cell = QSize(col_w, row_h)

        for i in range(c.count()):
            ic = c.itemIcon(i)
            if ic.isNull():
                ic = _placeholder_icon(px)
            tip = c.itemData(i, Qt.ToolTipRole)
            if tip is None:
                tip = ""
            btn = QToolButton(self._inner)
            btn.setAutoRaise(True)
            btn.setIcon(ic)
            btn.setIconSize(isz)
            btn.setFixedSize(cell)
            btn.setToolTip(str(tip))
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.NoFocus)
            r, co = divmod(i, _GRID_COLUMNS)
            self._grid.addWidget(btn, r, co)
            self._btn_group.addButton(btn, i)
            self._buttons.append(btn)

        cur = c.currentIndex()
        for i, btn in enumerate(self._buttons):
            btn.setChecked(i == cur and cur >= 0)

        def _on_btn(btn):
            bid = self._btn_group.id(btn)
            if bid >= 0:
                self._pick_index(bid)

        try:
            self._btn_group.buttonClicked.disconnect()
        except TypeError:
            pass
        self._btn_group.buttonClicked.connect(_on_btn)

        ideal = c._mw_ideal_grid_list_height()
        n = max(1, c.count())
        rows = (n + _GRID_COLUMNS - 1) // _GRID_COLUMNS
        _cw, rh = c._mw_cell_metrics()
        one_row_min = rh + _GRID_SPACING + 8
        cap = c._mw_max_list_height()
        if rows <= 2:
            list_h = min(ideal, cap)
        else:
            half_ideal = max(one_row_min, int(ideal * _POPUP_LIST_HEIGHT_FRACTION + 0.5))
            list_h = min(ideal, half_ideal, cap)
        need_scroll = ideal > list_h

        self._scroll.setFixedHeight(list_h)
        self._scroll.setMinimumHeight(list_h)
        self._scroll.setMaximumHeight(list_h)

        min_w = max(120, c.width()) + _POPUP_MIN_WIDTH_EXTRA
        inner_w = _GRID_COLUMNS * col_w + (_GRID_COLUMNS - 1) * _GRID_SPACING + self._grid.contentsMargins().left() + self._grid.contentsMargins().right() + 8
        w = max(min_w, inner_w)
        if need_scroll:
            w += 14
        self._inner.setMinimumWidth(inner_w)
        self.setFixedWidth(w)

        self.adjustSize()
        self._anchor_near_combo()

        if not self._app_filter_installed:
            QApplication.instance().installEventFilter(self)
            self._app_filter_installed = True
        self.show()
        self.raise_()
        self.activateWindow()
        QMetaObject.invokeMethod(
            self,
            "_mw_scroll_to_current_selection",
            Qt.QueuedConnection,
        )

    @pyqtSlot()
    def _mw_scroll_to_current_selection(self):
        """After layout: top if no selection or selection fits first page; else reveal selected cell."""
        if not self.isVisible():
            return
        sb = self._scroll.verticalScrollBar()
        sb.setValue(0)
        cur = self._combo.currentIndex()
        if cur < 0 or cur >= len(self._buttons):
            return
        btn = self._buttons[cur]
        vp_h = max(1, self._scroll.viewport().height())
        top = btn.y()
        bottom = top + btn.height()
        if bottom <= vp_h:
            return
        self._scroll.ensureWidgetVisible(btn, 4)

    def _anchor_near_combo(self):
        c = self._combo
        combo_tl = c.mapToGlobal(QPoint(0, 0))
        pw = self.width()
        ph = self.height()
        x = combo_tl.x()
        y = combo_tl.y() + c.height()
        scr = _application_screen_at_global(combo_tl)
        if scr is None:
            scr = QApplication.primaryScreen()
        if scr is not None and pw > 0 and ph > 0:
            ag = scr.availableGeometry()
            x = max(ag.left(), min(x, ag.right() - pw + 1))
            if y + ph > ag.bottom() + 1:
                y = combo_tl.y() - ph
            y = max(ag.top(), min(y, ag.bottom() - ph + 1))
        self.move(x, y)


class MarkerShapeCombo(QComboBox):
    """QGIS simple-marker previews in a wrapping 3-column grid."""

    def __init__(self, parent=None):
        super(MarkerShapeCombo, self).__init__(parent)
        self.setObjectName("cmbMarkerShape")
        self.setAttribute(Qt.WA_StyledBackground, True)
        fusion = QStyleFactory.create("Fusion")
        if fusion is not None:
            self.setStyle(fusion)
        self.setStyleSheet(_qss_marker_combo_stylesheet())
        self._mw_grid_popup = None
        self._mw_marker_natives = []
        self.rebuild_items()

    def changeEvent(self, event):
        super(MarkerShapeCombo, self).changeEvent(event)
        if event.type() == QEvent.EnabledChange:
            self.setStyleSheet(_qss_marker_combo_stylesheet())
            st = self.style()
            if st is not None:
                try:
                    st.unpolish(self)
                    st.polish(self)
                except Exception:
                    pass

    def _strip_combo_icon_from_style_option(self, opt):
        for name in ("currentIcon", "icon"):
            if hasattr(opt, name):
                try:
                    setattr(opt, name, QIcon())
                except (TypeError, AttributeError):
                    pass

    def paintEvent(self, event):
        style = self.style()
        if style is None:
            super(MarkerShapeCombo, self).paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        idx = self.currentIndex()
        saved_icon = self.itemIcon(idx) if idx >= 0 else QIcon()
        if saved_icon.isNull() and idx >= 0:
            fallback = getattr(opt, "currentIcon", QIcon())
            if not fallback.isNull():
                saved_icon = fallback
        self._strip_combo_icon_from_style_option(opt)
        style.drawComplexControl(QStyle.CC_ComboBox, opt, painter, self)
        field = style.subControlRect(
            QStyle.CC_ComboBox,
            opt,
            QStyle.SC_ComboBoxEditField,
            self,
        )
        if saved_icon.isNull():
            return
        if not field.isValid() or field.isEmpty():
            field = self.rect()
        sz = self.iconSize()
        mode = QIcon.Normal if self.isEnabled() else QIcon.Disabled
        state = QIcon.On if self.isEnabled() else QIcon.Off
        pm = saved_icon.pixmap(sz, mode, state)
        if pm is None or pm.isNull():
            pm = saved_icon.pixmap(sz)
        if pm is None or pm.isNull():
            return
        target = QRect(0, 0, sz.width(), sz.height())
        target.moveCenter(field.center())
        if not self.isEnabled():
            painter.setOpacity(0.55)
        painter.drawPixmap(target, pm)
        if not self.isEnabled():
            painter.setOpacity(1.0)

    def _mw_cell_metrics(self):
        cw = max(120, self.width())
        col_w = max(36, cw // _GRID_COLUMNS)
        px = self.iconSize().height()
        row_h = max(px + 8, col_w)
        return col_w, row_h

    def _mw_ideal_grid_list_height(self):
        n = max(1, self.count())
        rows = (n + _GRID_COLUMNS - 1) // _GRID_COLUMNS
        _col_w, row_h = self._mw_cell_metrics()
        inner = rows * row_h + max(0, rows - 1) * _GRID_SPACING + 8
        return max(row_h + 8, inner)

    def _mw_max_list_height(self):
        scr = _application_screen_at_global(self.mapToGlobal(QPoint(0, 0)))
        if scr is None:
            scr = QApplication.primaryScreen()
        if scr is None:
            return 560
        ag = scr.availableGeometry()
        combo_tl = self.mapToGlobal(QPoint(0, 0))
        room_below = ag.bottom() - (combo_tl.y() + self.height()) - 36
        room_above = combo_tl.y() - ag.top() - 36
        return max(160, min(max(room_below, room_above), ag.height() - 64))

    def rebuild_items(self):
        prev_marker = None
        if self.count() and self.currentIndex() >= 0:
            idx = self.currentIndex()
            olds = getattr(self, "_mw_marker_natives", None)
            if olds and idx < len(olds):
                prev_marker = olds[idx]
            else:
                d0 = self.currentData(Qt.UserRole)
                if d0 is not None:
                    prev_marker = coerce_marker_shape_for_setshape(d0)

        natives = _ordered_natives()
        self._mw_marker_natives = list(natives)

        px = _ICON_PIX
        self.blockSignals(True)
        self.clear()
        self.setIconSize(QSize(px, px))
        self.setMaxVisibleItems(min(32, max(8, len(natives))))

        for i, nat in enumerate(natives):
            ic = _shape_preview_icon_native(nat, px)
            if ic.isNull():
                ic = _placeholder_icon(px)
            self.addItem(ic, "")
            self.setItemData(i, i, Qt.UserRole)
            try:
                tip = str(QgsSimpleMarkerSymbolLayer.encodeShape(nat))
            except Exception:
                tip = ""
            self.setItemData(i, tip or "", Qt.ToolTipRole)

        if hasattr(QComboBox, "Ignore"):
            self.setSizeAdjustPolicy(QComboBox.Ignore)
        self.setMinimumContentsLength(0)

        if self.count() == 0:
            ic = _placeholder_icon(px)
            self.addItem(ic, "")
            fb = None
            for name in ("ShapeCircle", "ShapeSquare", "ShapeDiamond"):
                fb = getattr(QgsSimpleMarkerSymbolLayer, name, None)
                if fb is not None:
                    break
            self._mw_marker_natives = [fb] if fb is not None else []
            if self._mw_marker_natives:
                self.setItemData(0, 0, Qt.UserRole)
                try:
                    self.setItemData(0, str(QgsSimpleMarkerSymbolLayer.encodeShape(fb)), Qt.ToolTipRole)
                except Exception:
                    self.setItemData(0, "", Qt.ToolTipRole)

        ipick = _find_native_index(self._mw_marker_natives, prev_marker)
        if ipick >= 0:
            self.setCurrentIndex(ipick)
        else:
            self.setCurrentIndex(0 if self.count() else -1)

        self.blockSignals(False)

    def _select_shape_value(self, value):
        """Match combo row by stored index, encoded name, or native shape from the layer."""
        natives = getattr(self, "_mw_marker_natives", ())
        if isinstance(value, int) and not isinstance(value, bool):
            if 0 <= value < len(natives):
                self.setCurrentIndex(value)
                return
        if isinstance(value, str):
            dec = _decode_shape_string(value)
            if dec is not None:
                j = _find_native_index(list(natives), dec)
                if j >= 0:
                    self.setCurrentIndex(j)
                    return
        j = _find_native_index(list(natives), value)
        if j >= 0:
            self.setCurrentIndex(j)
            return
        try:
            coerced = coerce_marker_shape_for_setshape(value)
            j = _find_native_index(list(natives), coerced)
            if j >= 0:
                self.setCurrentIndex(j)
                return
        except Exception:
            pass
        self.setCurrentIndex(0 if natives else -1)

    def clearShapeSelection(self):
        self.blockSignals(True)
        try:
            self.setCurrentIndex(-1)
        finally:
            self.blockSignals(False)

    def currentMarkerShape(self):
        if self.currentIndex() < 0:
            return None
        idx = self.currentIndex()
        natives = getattr(self, "_mw_marker_natives", ())
        if 0 <= idx < len(natives):
            return natives[idx]
        d = self.currentData(Qt.UserRole)
        if d is None:
            return None
        return coerce_marker_shape_for_setshape(d)

    def setCurrentMarkerShape(self, shape):
        if shape is None:
            self.clearShapeSelection()
            return
        self._select_shape_value(shape)

    def showPopup(self):
        self.setStyleSheet(_qss_marker_combo_stylesheet())
        if self._mw_grid_popup is None:
            self._mw_grid_popup = _MarkerShapeGridPopup(self)
        self._mw_grid_popup.sync_and_show()

    def hidePopup(self):
        p = getattr(self, "_mw_grid_popup", None)
        if p is not None and p.isVisible():
            p.hide_for_combo()
        super(MarkerShapeCombo, self).hidePopup()
