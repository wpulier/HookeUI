"""Worker-local offscreen Qt panel bridge. No automatic QWidget adaptation.

The owner supplies confinement, resource admission, a serialized GUI-thread queue
and actual-paint authorization. This component alone cannot admit a browser event,
qualify an integration or publish scientific edits. It remains unavailable through
public discovery until the workspace interaction/persistence path is qualified.
"""
from contextlib import contextmanager
import copy
import hashlib
import json
import secrets
import sys
import threading
from .scientific_callback_errors import recoverable_callback
from . import scientific_native_panel_wire as wire


class Refused(ValueError):
    pass


class CallbackFailed(RuntimeError):
    pass


class BoundaryFailed(BaseException):
    """The owning worker must terminate; this is never recoverable panel state."""
    pass


@contextmanager
def callback_errors():
    errors=[];old_hook=sys.excepthook;overflow=False
    def capture(kind,error,traceback):
        nonlocal overflow
        if len(errors)<64:errors.append(error)
        else:overflow=True
    def checkpoint():
        if overflow:raise BoundaryFailed('Native callback error allowance exceeded')
        for error in errors:
            if not recoverable_callback(error):raise BoundaryFailed('Native callback state is unsafe to retain') from error
        if errors:
            cause=errors[0] if len(errors)==1 else ExceptionGroup('Native callback errors',errors)
            raise CallbackFailed('Native callback failed during panel interaction or painting') from cause
    sys.excepthook=capture
    try:
        try:yield checkpoint
        except BaseException as error:capture(type(error),error,error.__traceback__)
        checkpoint()
    finally:
        sys.excepthook=old_hook


class Panel:
    def __init__(self, widget):
        from qtpy.QtCore import Qt,qVersion
        from qtpy.QtGui import QAccessible
        from qtpy.QtWidgets import QApplication, QWidget
        app=QApplication.instance()
        if (threading.current_thread() is not threading.main_thread() or app is None
                or app.platformName()!='offscreen' or not isinstance(widget,QWidget)
                or widget.parentWidget() is not None):
            raise Refused('A panel needs its owning offscreen GUI thread and explicit root widget')
        self.widget=widget;self.app=app;self.thread=threading.get_ident()
        # These frames are retained until the next explicit interaction. A
        # blinking caret changes pixels without changing the control, making a
        # perfectly current text edit look stale. Keep the caret visible in this
        # private offscreen application; actual pixel/state changes still revoke
        # the receipt. This never changes the desktop's cursor preferences.
        app.setCursorFlashTime(0)
        # This bridge is the accessibility client for its private offscreen app.
        # Without activation Qt suppresses model-change cache updates: removing
        # and then inserting a list row can return its invalid former interface.
        # Activate before querying any interfaces; never replay a user action.
        QAccessible.setActive(True)
        self.identity=secrets.token_hex(16);self.current=None;self.fingerprint=None;self.closed=False;self.generation=0
        try:
            with callback_errors():
                # Qt 6.12.0's QStyleSheetStyle::subControlRect cuts the edit
                # field at x=0 for NoButtons: its absent button rectangles are
                # still right-aligned. Align only those absent subcontrols left
                # so the full numeric value is painted. Keep the raster style;
                # macOS's native style omits controls on the offscreen platform.
                # https://github.com/qt/qtbase/blob/v6.12.0/src/widgets/styles/qstylesheetstyle.cpp
                if qVersion() == '6.12.0':
                    rule = ('QAbstractSpinBox[buttonSymbols="NoButtons"]::up-button, '
                            'QAbstractSpinBox[buttonSymbols="NoButtons"]::down-button '
                            '{subcontrol-position: left;}')
                    if rule not in app.styleSheet():app.setStyleSheet(app.styleSheet()+'\n'+rule)
                widget.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen,True)
                widget.show()
        except BaseException as error:
            self.closed=True
            try:
                with callback_errors():
                    try:widget.hide()
                    finally:widget.deleteLater()
            except BaseException as cleanup:
                raise BoundaryFailed('Native panel initialization and cleanup failed') from BaseExceptionGroup(
                    'Native panel lifecycle failures',[error,cleanup])
            raise

    def check(self):
        if self.closed or self.thread!=threading.get_ident():
            raise Refused('The native panel is closed or belongs to another GUI thread')

    def invalidate(self):
        self.check();self.current=None;self.generation+=1

    def accessibility(self):
        from qtpy.QtGui import QAccessible
        from qtpy.QtCore import QPoint
        interface=QAccessible.queryAccessibleInterface(self.widget)
        if interface is None or not interface.isValid():
            raise Refused('This panel needs accessible integration metadata')
        origin=self.widget.mapToGlobal(QPoint(0,0))
        rows=[];pending=[(interface,None,0)];seen=set()
        while pending:
            current,parent,depth=pending.pop()
            identity=int(QAccessible.uniqueId(current))
            if identity in seen or len(rows)>=512 or depth>16:
                raise Refused('Native accessibility exceeds its bounded tree allowance')
            seen.add(identity)
            if not current.isValid():raise Refused('The native accessibility tree changed')
            state=current.state()
            if state.passwordEdit:
                raise Refused('Credentials use the application key sheet, not a contributed native panel')
            rect=current.rect()
            row={'id':str(identity),'parent':parent,'role':current.role().name,
                'name':current.text(QAccessible.Text.Name),'description':current.text(QAccessible.Text.Description),
                'value':current.text(QAccessible.Text.Value),
                'rect':[rect.x()-origin.x(),rect.y()-origin.y(),rect.width(),rect.height()],
                'disabled':bool(state.disabled),'focused':bool(state.focused),
                'focusable':bool(state.focusable),'invisible':bool(state.invisible),
                'checked':bool(state.checked),'checkable':bool(state.checkable),'readOnly':bool(state.readOnly)}
            if any(len(row[key])>1000 for key in ('name','description','value')):
                raise Refused('Native accessibility text exceeds its display allowance')
            rows.append(row)
            children=current.childCount()
            if not 0<=children<=512-len(rows):raise Refused('Too many native accessibility children')
            for index in range(children-1,-1,-1):
                child=current.child(index)
                if child is None:raise Refused('The native accessibility tree changed')
                pending.append((child,str(identity),depth+1))
        if len(json.dumps(rows,ensure_ascii=False).encode())>64<<10:
            raise Refused('Native accessibility exceeds its message allowance')
        return rows

    def paint(self,width,height):
        from qtpy.QtCore import QPoint
        from qtpy.QtGui import QImage,QPainter
        from qtpy.QtWidgets import QWidget
        self.check()
        if any(type(n) is not int or not 1<=n<=1024 for n in (width,height)):
            raise Refused('Panel dimensions must be between 1 and 1024 logical pixels')
        if width*height*32>32<<20:raise Refused('Panel exceeds its working memory allowance')
        previous=self.current
        generation=self.generation
        self.current=None
        try:
            with callback_errors():
                self.widget.resize(width,height);self.widget.ensurePolished()
                if self.widget.layout():self.widget.layout().activate()
                self.app.processEvents()
                if (self.widget.width(),self.widget.height())!=(width,height):
                    raise Refused('This panel needs a larger qualified viewport')
                if any(w is not self.widget and w.isVisible() for w in self.app.topLevelWidgets()):
                    raise Refused('This native dialog needs an explicit workspace interaction integration')
                widgets=[self.widget,*self.widget.findChildren(QWidget)]
                if len(widgets)>512:raise Refused('The panel exceeds its native widget allowance')
                image=QImage(width,height,QImage.Format.Format_RGBA8888);image.fill(0)
                painter=QPainter(image)
                try:self.widget.render(painter,QPoint(0,0))
                finally:painter.end()
                pixels=bytes(image.constBits())
                if len(pixels)!=width*height*4:raise Refused('Unexpected native panel stride')
                focus=self.app.focusWidget()
                state={'format':'scientific-native-panel-v1','panel':self.identity,
                    'width':width,'height':height,'pixelRatio':1,
                    'accessibility':self.accessibility(),
                    'focus':focus.accessibleName() if focus is not None and (focus is self.widget or self.widget.isAncestorOf(focus)) else None}
        except BaseException:
            self.fingerprint=None
            raise
        fingerprint=hashlib.sha256(pixels+repr(state).encode()).hexdigest()
        # Identical pixels after an action must not revive a consumed request.
        state['frame']=previous['frame'] if previous is not None and generation==self.generation and fingerprint==self.fingerprint else secrets.token_hex(32)
        self.fingerprint=fingerprint
        self.current=wire.frame(state,pixels)
        return copy.deepcopy(self.current),pixels

    def _point(self,value):
        from qtpy.QtCore import QPoint
        if (type(value) is not list or len(value)!=2 or any(type(n) is not int for n in value)
                or not 0<=value[0]<self.current['width'] or not 0<=value[1]<self.current['height']):
            raise Refused('The pointer must be inside the exact rendered panel')
        return QPoint(*value)

    def dispatch(self,request):
        from qtpy.QtCore import QEvent,QPoint,QPointF,Qt
        from qtpy.QtGui import QMouseEvent,QInputMethodEvent,QKeyEvent,QWheelEvent
        from qtpy.QtWidgets import QLineEdit,QTextEdit,QPlainTextEdit,QAbstractSpinBox
        self.check()
        if (type(request) is not dict or set(request)!={'panel','frame','op','value'} or self.current is None
                or request['panel']!=self.identity or request['frame']!=self.current['frame']):
            raise Refused('Wait for the current native panel before interacting')
        try:
            request=wire.command(request,self.current)
        except ValueError as error:
            raise Refused(str(error)) from error
        # Detect intervening native changes before any input is delivered.
        previous=self.current
        state,_=self.paint(previous['width'],previous['height'])
        if state!=previous:raise Refused('The native panel changed; inspect it before trying again')
        op=request['op'];value=request['value'];target=None
        if op in ('click','drag','wheel'):
            position = value if op=='click' else value['points'][0] if op=='drag' else value['point']
            point=self._point(position);target=self.widget.childAt(point) or self.widget
            if not target.isEnabled() or not target.isVisible():raise Refused('The control is unavailable')
        elif op=='text':
            target=self.app.focusWidget()
            # Qt gives a spin box focus on behalf of its internal editor. Send
            # text through that editor so its native validator remains in charge.
            if isinstance(target,QAbstractSpinBox) and not target.isReadOnly():
                target=target.lineEdit()
            if (type(value) is not str or len(value)>4000 or not value
                    or any(ord(c)<32 and c not in '\n\t' for c in value)
                    or not isinstance(target,(QLineEdit,QTextEdit,QPlainTextEdit))
                    or not self.widget.isAncestorOf(target) or target.isReadOnly()
                    or not target.isEnabled() or not target.isVisible()):
                raise Refused('Focus an editable text control before entering bounded text')
        elif op=='key':
            # Deliberately exclude platform shortcuts and the system clipboard.
            # Integrations needing file selection use authorized resource refs.
            keys = {name: getattr(Qt.Key, 'Key_' + name) for name in wire.KEYS}
            target=self.app.focusWidget()
            if (type(value) is not str or value not in keys or target is None
                    or not (target is self.widget or self.widget.isAncestorOf(target))
                    or not target.isEnabled() or not target.isVisible()):
                raise Refused('Focus a current panel control and use a supported navigation key')
            key=keys[value]
        else:raise Refused('Choose a declared panel interaction')
        self.current=None
        with callback_errors() as checkpoint:
            if op in ('click','drag'):
                # Direct delivery bypasses Qt's platform mouse dispatcher. Restore
                # its click-focus behavior inside this offscreen root only.
                self.app.setActiveWindow(self.widget)
                checkpoint()
                if target.focusPolicy() & Qt.FocusPolicy.ClickFocus:
                    target.setFocus(Qt.FocusReason.MouseFocusReason)
                    checkpoint()
                button = Qt.MouseButton.RightButton if op=='drag' and value['button']=='right' else Qt.MouseButton.LeftButton
                def mouse(kind,position,pressed):
                    local=QPointF(target.mapFrom(self.widget,position))
                    global_point=QPointF(target.mapToGlobal(local.toPoint()))
                    event=QMouseEvent(kind,local,global_point,
                        Qt.MouseButton.NoButton if kind==QEvent.Type.MouseMove else button,
                        button if pressed else Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier)
                    self.app.sendEvent(target,event)
                    checkpoint()
                completed=False
                try:
                    mouse(QEvent.Type.MouseButtonPress,point,True)
                    if op=='drag':
                        for position in value['points'][1:]:
                            mouse(QEvent.Type.MouseMove,QPoint(*position),True)
                    if op=='click' or not value['cancel']:
                        mouse(QEvent.Type.MouseButtonRelease,point if op=='click' else QPoint(*value['points'][-1]),False)
                        completed=True
                except BaseException as error:
                    # Qt can propagate override errors directly, as well as send
                    # slot errors to excepthook. Neither fatal path may deliver
                    # another plugin event; the owner terminates the worker.
                    if not recoverable_callback(error):completed=True
                    raise
                finally:
                    if not completed:
                        # No synthetic release/click on interruption. Private changes
                        # already made by callbacks remain; this is not an undo.
                        self.app.sendEvent(target,QEvent(QEvent.Type.UngrabMouse))
            elif op=='wheel':
                local=QPointF(target.mapFrom(self.widget,point))
                global_point=QPointF(target.mapToGlobal(local.toPoint()))
                self.app.sendEvent(target,QWheelEvent(local,global_point,QPoint(0,0),QPoint(*value['angleDelta']),
                    Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False))
            elif op=='key':
                modifiers = Qt.KeyboardModifier.ShiftModifier if value=='Backtab' else Qt.KeyboardModifier.NoModifier
                for kind in (QEvent.Type.KeyPress,QEvent.Type.KeyRelease):
                    self.app.sendEvent(target,QKeyEvent(kind,key,modifiers,' ' if value=='Space' else ''))
                    checkpoint()
            else:
                event=QInputMethodEvent();event.setCommitString(value);self.app.sendEvent(target,event)
            checkpoint()
            self.app.processEvents()

    def close(self):
        if self.closed:return
        self.check();self.current=None;self.fingerprint=None;self.closed=True
        with callback_errors():
            try:self.widget.close()
            finally:
                try:self.widget.hide()
                finally:self.widget.deleteLater()
