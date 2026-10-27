"""Actual native panel component under OS confinement; not a public UI qualification."""
import importlib.util,json,sys
from pathlib import Path

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module;spec.loader.exec_module(module);return module

import types
package=types.ModuleType('env_worker');package.__path__=[];sys.modules['env_worker']=package
load('env_worker.scientific_callback_errors',sys.argv[4])
load('env_worker.scientific_native_panel_wire',sys.argv[5])
bridge=load('env_worker.scientific_native_panel',sys.argv[1]);runtime=load('napari_cgl',sys.argv[2])
runtime._runtime(runtime.REFERENCE_REVISION)
from qtpy.QtCore import QPoint,Qt,QTimer,Signal
from qtpy.QtGui import QPainter,QColor
from qtpy.QtWidgets import QApplication,QWidget,QVBoxLayout,QTabWidget,QPushButton,QLabel,QLineEdit,QDoubleSpinBox
import numpy as np
from napari.components import ViewerModel

app=QApplication.instance() or QApplication([])
app.setCursorFlashTime(1000)
viewer=ViewerModel();source=np.arange(24,dtype='uint16').reshape(4,6)
layer=viewer.add_image(source.copy(),name='Source');calls=[]

class Plot(QWidget):
    gesture = Signal(str)
    def __init__(self):
        super().__init__();self.selected=None;self.bad=False;self.failure=None;self.setMinimumHeight(100);self.setAccessibleName('Scientific profile')
    def paintEvent(self,event):
        if self.bad:raise RuntimeError('controlled paint failure')
        painter=QPainter(self)
        painter.fillRect(self.rect(),QColor('#112233'))
        painter.setPen(QColor('#00ff88'))
        painter.drawLine(10,self.height()-10,self.width()-10,10)
        if self.selected is not None:painter.drawEllipse(self.selected,6,6)
        painter.end()
    def event(self,event):
        from qtpy.QtCore import QEvent
        if event.type()==QEvent.Type.UngrabMouse:
            calls.append(('cancel',));return True
        return super().event(event)
    def mousePressEvent(self,event):
        calls.append(('press',event.position().x(),event.position().y()))
        self.gesture.emit('press')
        if self.failure=='press':raise ValueError('Controlled failed native press')
        if self.failure=='fatal-press':raise MemoryError('Controlled fatal native press')
    def mouseMoveEvent(self,event):
        calls.append(('move',event.position().x(),event.position().y()))
        self.gesture.emit('move')
        if self.failure=='move':raise ValueError('Controlled failed native move')
    def wheelEvent(self,event):
        calls.append(('wheel',event.angleDelta().y()));event.accept()
    def mouseReleaseEvent(self,event):
        self.selected=event.position().toPoint();calls.append(('plot',self.selected.x(),self.selected.y()));self.update()

root=QWidget();layout=QVBoxLayout(root);tabs=QTabWidget();layout.addWidget(tabs)
first=QWidget();form=QVBoxLayout(first);label=QLabel('Choose a setting');name=QLineEdit();name.setAccessibleName('Layer title')
amount=QDoubleSpinBox();amount.setRange(0,1);amount.setValue(.35);amount.setAccessibleName('Layer opacity')
button=QPushButton('Apply to selected layer');button.setAccessibleName('Apply to selected layer')
plot=Plot()
def signal_failure(stage):
    if plot.failure == 'signal-' + stage:raise ValueError('Controlled Qt slot failure')
    if plot.failure == 'fatal-signal-' + stage:raise MemoryError('Controlled fatal Qt slot failure')
plot.gesture.connect(signal_failure)
for child in (label,name,amount,button,plot):form.addWidget(child)
second=QWidget();second_layout=QVBoxLayout(second);second_layout.addWidget(QLabel('Another nested page'))
tabs.addTab(first,'Scientific controls');tabs.addTab(second,'Information')
def apply():
    layer.opacity=amount.value();label.setText(f'Applied {layer.opacity}');calls.append(('apply',layer.opacity))
button.clicked.connect(apply)
panel=bridge.Panel(root)
def changed(event):panel.invalidate()
layer.events.connect(changed)
def frame():return panel.paint(440,420)
def click(widget,local=None):
    state,_=frame();point=widget.mapTo(root,local or widget.rect().center())
    request={'panel':state['panel'],'frame':state['frame'],'op':'click','value':[point.x(),point.y()]}
    panel.dispatch(request);return request
def refused(call):
    try:call()
    except bridge.Refused:return
    raise AssertionError('Expected refusal')

try:
    # Model rows are replaced by ordinary specialist controls (keyframes,
    # measurements, alternatives). Offscreen Qt must invalidate removed cells.
    from qtpy.QtWidgets import QListWidget,QTableWidget,QTableWidgetItem
    root.hide()
    dynamic=QWidget();dynamic_layout=QVBoxLayout(dynamic)
    entries=QListWidget();entries.addItems(['Frame zero','Removed frame'])
    table=QTableWidget(2,1);table.setItem(0,0,QTableWidgetItem('Object 7'))
    table.setItem(1,0,QTableWidgetItem('Removed object'))
    dynamic_layout.addWidget(entries);dynamic_layout.addWidget(table)
    dynamic_panel=bridge.Panel(dynamic)
    try:
        dynamic_panel.paint(440,420)
        entries.takeItem(1);table.removeRow(1)
        dynamic_panel.paint(440,420)
        entries.addItem('New frame');table.insertRow(1)
        table.setItem(1,0,QTableWidgetItem('Object 9007199254740993'))
        state,pixels=dynamic_panel.paint(440,420)
        names=[row['name'] for row in state['accessibility']]
        assert 'New frame' in names and 'Object 9007199254740993' in names
        assert 'Removed frame' not in names and 'Removed object' not in names
        assert dynamic_panel.paint(440,420)==(state,pixels)
    finally:
        dynamic_panel.close();root.show()
    # Scroll containers are common in real specialist panels. Deliver a command
    # after a human pause, rather than only immediately after taking its frame.
    from qtpy.QtWidgets import QScrollArea,QComboBox
    from qtpy.QtTest import QTest
    root.hide()
    scroll=QScrollArea();scroll.setWidgetResizable(True)
    content=QWidget();fields=QVBoxLayout(content)
    from magicgui.widgets import FloatSlider
    slider=FloatSlider(value=.4,min=0,max=1);fields.addWidget(slider.native)
    choice=QComboBox();choice.addItems(['Prepared model','Alternative model']);fields.addWidget(choice)
    for index in range(24):fields.addWidget(QLineEdit(f'Setting {index}'))
    run=QPushButton('Run analysis');fields.addWidget(run)
    scroll.setWidget(content);scroll_panel=bridge.Panel(scroll)
    state,first_pixels=scroll_panel.paint(480,420)
    def visible_text(widget,pixels,clear,restore):
        point=widget.mapTo(scroll,QPoint(0,0))
        geometry=widget.geometry()
        def region(data):
            raster=np.frombuffer(data,dtype='uint8').reshape(420,480,4)
            return raster[point.y()+2:point.y()+widget.height()-2,point.x()+2:point.x()+widget.width()-2,:3]
        try:
            clear();_,blank=scroll_panel.paint(480,420)
            assert widget.geometry()==geometry, 'Blank-text oracle changed control geometry'
            assert np.count_nonzero(np.any(region(pixels)!=region(blank),axis=2))>8, 'Native control text is not painted'
        finally:
            restore()
        scroll_panel.paint(480,420)
    visible_text(choice,first_pixels,lambda:choice.setItemText(0,''),lambda:choice.setItemText(0,'Prepared model'))
    readout=slider.native.findChild(QDoubleSpinBox)
    assert readout.lineEdit().width()>=readout.fontMetrics().horizontalAdvance(readout.text()), 'Numeric value is clipped'
    text=readout.text()
    _,numeric_pixels=scroll_panel.paint(480,420)
    visible_text(readout.lineEdit(),numeric_pixels,lambda:readout.lineEdit().setText(''),lambda:readout.lineEdit().setText(text))
    state,first_pixels=scroll_panel.paint(480,420)
    repeated,repeated_pixels=scroll_panel.paint(480,420)
    assert repeated==state and repeated_pixels==first_pixels, 'Unchanged numeric controls repaint differently'
    runs=[]
    run.clicked.connect(lambda:runs.append(True))
    try:
        scroll_panel.paint(480,420)
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        state,button_pixels=scroll_panel.paint(480,420)
        visible_text(run,button_pixels,lambda:run.setText(''),lambda:run.setText('Run analysis'))
        state,_=scroll_panel.paint(480,420)
        QTest.qWait(700)
        point=run.mapTo(scroll,run.rect().center())
        scroll_panel.dispatch({'panel':state['panel'],'frame':state['frame'],'op':'click','value':[point.x(),point.y()]})
        assert runs==[True]
    finally:
        scroll_panel.close();root.show()
    class FailedLifecycle(QWidget):
        def __init__(self,stage):
            super().__init__();self.stage=stage
        def showEvent(self,event):
            if self.stage=='show':raise ValueError('Controlled native show failure')
            super().showEvent(event)
        def closeEvent(self,event):
            if self.stage=='close':raise MemoryError('Controlled native close failure')
            super().closeEvent(event)
    lifecycle_case = sys.argv[6] if len(sys.argv)>6 else 'all'
    broken=FailedLifecycle('show')
    if lifecycle_case!='gestures':
        try:bridge.Panel(broken)
        except bridge.CallbackFailed:pass
        else:raise AssertionError('Native show failure was swallowed')
        assert not broken.isVisible()
    if lifecycle_case!='gestures':
        closing=bridge.Panel(FailedLifecycle('close'))
        try:closing.close()
        except bridge.BoundaryFailed:pass
        else:raise AssertionError('Native fatal close failure was swallowed')
        assert closing.closed and closing.current is None and not closing.widget.isVisible()
    state,before=frame();assert len(set(before))>16
    names={row['name'] for row in state['accessibility']}
    assert {'Layer title','Layer opacity','Apply to selected layer','Scientific profile'} <= names,names
    request=click(button);assert calls==[('apply',.35)] and layer.opacity==.35
    refused(lambda:panel.dispatch(request));assert len(calls)==1
    _,after=frame();assert after!=before
    request=click(button);frame()
    refused(lambda:panel.dispatch(request));assert len(calls)==2
    click(plot,QPoint(50,40));assert calls[-1]==('plot',50,40)
    first=plot.mapTo(root,QPoint(30,25));last=plot.mapTo(root,QPoint(80,45))
    points=[[first.x(),first.y()],[last.x(),last.y()]]
    for cancel in (False,True):
        state,_=frame();start=len(calls)
        request={'panel':state['panel'],'frame':state['frame'],'op':'drag','value':{'points':points,'button':'left','cancel':cancel}}
        panel.dispatch(request)
        assert calls[start:]==[('press',30.,25.),('move',80.,45.),('cancel',) if cancel else ('plot',80,45)]
        refused(lambda:panel.dispatch(request))
    for failure in ('press','move','fatal-press','signal-press','signal-move','fatal-signal-press'):
        state,_=frame();plot.failure=failure;start=len(calls)
        request={'panel':state['panel'],'frame':state['frame'],'op':'drag','value':{'points':points,'button':'left','cancel':False}}
        try:panel.dispatch(request)
        except (bridge.CallbackFailed,bridge.BoundaryFailed) as error:
            assert isinstance(error,bridge.BoundaryFailed)==failure.startswith('fatal-')
        else:raise AssertionError('Native gesture failure was hidden')
        assert all(call[0]!='plot' for call in calls[start:]),calls[start:]
        assert len([call for call in calls[start:] if call[0]=='move'])==int(failure.endswith('move')),calls[start:]
        if not failure.startswith('fatal-'):assert calls[-1]==('cancel',),calls[start:]
        else:assert calls[start:]==[('press',30.,25.)],calls[start:]
        assert panel.current is None
        plot.failure=None
    state,_=frame()
    panel.dispatch({'panel':state['panel'],'frame':state['frame'],'op':'wheel' ,'value':{'point':points[0],'angleDelta':[0,120]}})
    assert calls[-1]==('wheel',120)
    click(name);state,_=frame()
    # A user can pause while composing text. A blinking insertion caret must
    # not invalidate otherwise unchanged native controls and their authority.
    from qtpy.QtTest import QTest
    QTest.qWait(550)
    panel.dispatch({'panel':state['panel'],'frame':state['frame'],'op':'text','value':'Measured µm'})
    assert name.text()=='Measured µm'
    state,_=frame();assert any(row['name']=='Layer title' and row['value']=='Measured µm' and row['focused'] for row in state['accessibility'])
    # Native navigation and editing must use the focused widget; no clipboard bridge.
    state,_=frame()
    panel.dispatch({'panel':state['panel'],'frame':state['frame'],'op':'key','value':'Backspace'})
    assert name.text()=='Measured µ'
    state,_=frame()
    panel.dispatch({'panel':state['panel'],'frame':state['frame'],'op':'key','value':'Tab'})
    assert amount.hasFocus() or amount.isAncestorOf(app.focusWidget())
    # Qt numeric controls own focus while their internal editor receives text.
    # Keep native validation and reject read-only controls before delivery.
    amount.selectAll()
    state,_=frame()
    panel.dispatch({'panel':state['panel'],'frame':state['frame'],'op':'text','value':'0.72'})
    assert amount.value()==.72
    amount.setReadOnly(True);state,_=frame()
    refused(lambda:panel.dispatch({'panel':state['panel'],'frame':state['frame'],'op':'text','value':'0.1'}))
    assert amount.value()==.72
    amount.setReadOnly(False)
    state,_=frame()
    refused(lambda:panel.dispatch({'panel':state['panel'],'frame':state['frame'],'op':'key','value':'Ctrl+V'}))
    state,_=frame();button.setEnabled(False)
    refused(lambda:panel.dispatch({'panel':state['panel'],'frame':state['frame'],'op':'click','value':[20,20]}))
    button.setEnabled(True)
    state,_=frame();layer.opacity=.45
    refused(lambda:panel.dispatch({'panel':state['panel'],'frame':state['frame'],'op':'click','value':[20,20]}))
    state,_=frame();before_calls=list(calls)
    QTimer.singleShot(0,lambda:setattr(layer,'opacity',.65))
    point=button.mapTo(root,button.rect().center())
    refused(lambda:panel.dispatch({'panel':state['panel'],'frame':state['frame'],'op':'click','value':[point.x(),point.y()]}))
    assert calls==before_calls and layer.opacity==.65
    click(tabs.tabBar(),tabs.tabBar().tabRect(1).center());assert tabs.currentIndex()==1
    click(tabs.tabBar(),tabs.tabBar().tabRect(0).center());assert tabs.currentIndex()==0
    def fail():raise RuntimeError('controlled native callback failure')
    button.clicked.connect(fail)
    try:click(button)
    except bridge.CallbackFailed:pass
    else:raise AssertionError('Callback failure was hidden')
    assert panel.current is None
    button.clicked.disconnect(fail);click(button)
    plot.bad=True
    try:frame()
    except bridge.CallbackFailed:pass
    else:raise AssertionError('Paint failure was hidden')
    assert panel.current is None
    plot.bad=False;frame()
    QTimer.singleShot(0,fail)
    try:frame()
    except bridge.CallbackFailed:pass
    else:raise AssertionError('Queued callback failure was hidden')
    assert panel.current is None
    frame()
    # An ordinary error cannot hide a later fatal Qt callback in the same event drain.
    def fatal():raise MemoryError('Controlled native allocation refusal')
    QTimer.singleShot(0,fail);QTimer.singleShot(0,fatal)
    try:frame()
    except bridge.BoundaryFailed as error:assert isinstance(error.__cause__,MemoryError)
    else:raise AssertionError('Fatal callback was hidden behind an ordinary error')
    assert panel.current is None
    assert np.array_equal(layer.data,source)
    try:Path(sys.argv[1]).write_text('Runtime modification must be denied')
    except PermissionError:pass
    else:raise AssertionError('Runtime source was writable')
    try:Path(sys.argv[3]).read_bytes()
    except PermissionError:pass
    else:raise AssertionError('Unrelated protected file was readable')
    import socket
    try:
        connection=socket.create_connection(('127.0.0.1',9),timeout=1)
    except PermissionError:pass
    else:
        connection.close();raise AssertionError('Network access was not denied')
    print(json.dumps({'ok':True,'nativeCallbacks':calls,'text':name.text(),'pixels':len(after),
        'nestedTabs':True,'keyboardNavigation':True,'dragWheelAndCancellation':True,'failedGestureStopsBeforeRelease':True,'lifecycleErrorsCaptured':True,'nativeAccessibilityNamesValuesAndFocus':True,'syntheticInMemoryArrayUnchanged':True,'projectSaveReopen':'not exercised',
        'nativeContextInvalidated':True,'paintAndQueuedFailureRecovery':True,'fileAndNetworkDenied':True}))
finally:
    layer.events.disconnect(changed)
    panel.close()
