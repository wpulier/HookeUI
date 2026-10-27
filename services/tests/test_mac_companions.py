"""Companion candidate approval and transport integrity; synthetic ZIP bytes, no public writes."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest

MACOS = Path(__file__).resolve().parents[2] / 'packaging/macos'

@pytest.fixture
def companions(monkeypatch):
    monkeypatch.syspath_prepend(str(MACOS))
    return importlib.import_module('release_companions')

def manifest(tmp_path):
    folder = tmp_path / 'authored'; folder.mkdir()
    body = b'synthetic package'; (folder/'tool.zip').write_bytes(body)
    value = {'format':1,'sourceCommit':'a'*40,'assets':[{'name':'tool.zip','sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}]}
    path = folder/'input.json'; path.write_text(json.dumps(value))
    return path, value

def test_copy_approval_and_tamper(companions, tmp_path):
    source, value = manifest(tmp_path); out = tmp_path/'candidate'; out.mkdir()
    binding = companions.stage(source, out)
    assert binding['sourceCommit'] == 'a'*40
    assert (out/'companions/companions.json').read_bytes() == source.read_bytes()
    with pytest.raises(ValueError, match='approval'):
        companions.verify(out, binding, None)
    companions.verify(out, binding, binding['sha256'])
    (source.parent/'tool.zip').write_bytes(b'author changed it')
    companions.verify(out, binding, binding['sha256'])
    (out/'companions/tool.zip').chmod(0o644); (out/'companions/tool.zip').write_bytes(b'tampered')
    with pytest.raises(ValueError): companions.verify(out, binding, binding['sha256'])

@pytest.mark.parametrize('change', ['escape','option','duplicate','symlink','size','hash','extra-key','bool-size'])
def test_bad_manifest_refuses_before_copy(companions, tmp_path, change):
    path, value = manifest(tmp_path); row=value['assets'][0]
    if change=='escape': row['name']='../tool.zip'
    if change=='option': row['name']='--tool.zip'
    if change=='duplicate': value['assets'].append(dict(row))
    if change=='symlink':
        (path.parent/'tool.zip').rename(path.parent/'real.zip'); (path.parent/'tool.zip').symlink_to('real.zip')
    if change=='size': row['bytes']+=1
    if change=='hash': row['sha256']='0'*64
    if change=='extra-key': row['url']='https://untrusted.invalid'
    if change=='bool-size': row['bytes']=True
    path.write_text(json.dumps(value)); out=tmp_path/'out';out.mkdir()
    with pytest.raises((ValueError,OSError)): companions.stage(path,out)
    assert not (out/'companions').exists()

def test_unlisted_candidate_and_missing_binding_refuse(companions,tmp_path):
    source,_=manifest(tmp_path);out=tmp_path/'out';out.mkdir();binding=companions.stage(source,out)
    (out/'companions').chmod(0o755);(out/'companions/other.zip').write_bytes(b'not approved')
    with pytest.raises(ValueError):companions.verify(out,binding,binding['sha256'])
    with pytest.raises(ValueError):companions.verify(out,None,None)

def test_promotion_cannot_omit_or_change_beta_companions(companions):
    held={'manifest':'companions.json','sha256':'a'*64,'sourceCommit':'b'*40}
    companions.promotion(None,None);companions.promotion(held,held)
    for candidate in (None,{**held,'sha256':'c'*64}):
        with pytest.raises(ValueError):companions.promotion(held,candidate)
    with pytest.raises(ValueError):companions.promotion(None,held)

def test_downloaded_and_anonymous_transport_checked_against_frozen_candidate(companions,tmp_path):
    import io
    source,_=manifest(tmp_path);out=tmp_path/'out';out.mkdir();binding=companions.stage(source,out)
    downloaded=tmp_path/'download';downloaded.mkdir()
    for name in ('companions.json','tool.zip'):(downloaded/name).write_bytes((out/'companions'/name).read_bytes())
    companions.verify_download(out,binding,downloaded)
    (downloaded/'tool.zip').write_bytes(b'corrupt')
    with pytest.raises(ValueError):companions.verify_download(out,binding,downloaded)
    calls=[]
    def fetch(url,timeout):
        calls.append(url);return io.BytesIO((out/'companions'/url.rsplit('/',1)[1]).read_bytes())
    companions.verify_remote(out,binding,'https://github.com/owner/repo/releases/download/v1',fetch=fetch)
    assert len(calls)==2
    with pytest.raises(ValueError):companions.verify_remote(out,binding,'https://github.com/owner/repo/releases/download/v1',fetch=lambda *a,**k:io.BytesIO(b'wrong'))

@pytest.fixture
def release(monkeypatch):
    import importlib.util
    monkeypatch.syspath_prepend(str(MACOS))
    spec=importlib.util.spec_from_file_location('companion_release',MACOS/'release-channel.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def test_prepare_binds_snapshot_and_promotion_before_build(release,companions,tmp_path,monkeypatch):
    from argparse import Namespace
    from contextlib import contextmanager
    source,_=manifest(tmp_path);out=tmp_path/'out'
    args=Namespace(channel='beta',version='0.2.2',commit='c'*40,promote_from=None,evidence='https://example.test',out=out,companions=source)
    monkeypatch.setattr(release,'require_source',lambda channel,c:None)
    monkeypatch.setattr(release,'next_release_build',lambda c:4000)
    @contextmanager
    def checkout(c):yield tmp_path
    monkeypatch.setattr(release,'source_checkout',checkout)
    def run(*args,**kwargs):
        if args[0]=='git':return '3000'
        # Author edits during the build cannot affect the frozen assets.
        (source.parent/'tool.zip').write_bytes(b'changed during app build')
        out.mkdir();(out/'app.dmg').write_bytes(b'dmg')
        (out/'release.json').write_text(json.dumps({'channel':'beta','bundleId':release.CHANNELS['beta']['bundleId'],'revision':'c'*40,'version':'0.2.2','build':4000,'notarized':True,'appcast':True,'dmg':'app.dmg','dmgSha256':hashlib.sha256(b'dmg').hexdigest()}))
    monkeypatch.setattr(release,'run',run);release.prepare(args)
    receipt=json.loads((out/'release.json').read_text());intent=json.loads((out/'release-intent.json').read_text())
    assert receipt['companions']==intent['companions']
    assert receipt['revision']!=receipt['companions']['sourceCommit']
    assert (out/'companions/tool.zip').read_bytes()==b'synthetic package'
    args.channel='stable';args.out=tmp_path/'stable';args.promote_from='0.2.2';args.companions=None
    monkeypatch.setattr(release,'published_beta',lambda v:receipt)
    with pytest.raises(ValueError,match='preserve'):release.prepare(args)
    assert not args.out.exists()

def test_publish_requires_separate_approval_before_transport(release,companions,tmp_path,monkeypatch):
    from argparse import Namespace
    source,_=manifest(tmp_path);out=tmp_path/'out';out.mkdir();binding=companions.stage(source,out)
    intent={'channel':'beta','revision':'c'*40,'version':'0.2.2','build':4000,'promote_from':None,'companions':binding}
    receipt={**intent,'bundleId':release.CHANNELS['beta']['bundleId'],'notarized':True,'appcast':True,'dmg':'app.dmg','dmgSha256':hashlib.sha256(b'dmg').hexdigest()}
    (out/'app.dmg').write_bytes(b'dmg');(out/'release.json').write_text(json.dumps(receipt));(out/'release-intent.json').write_text(json.dumps(intent))
    calls=[];monkeypatch.setattr(release,'run',lambda *a,**k:calls.append(a))
    monkeypatch.setattr(release,'require_source',lambda channel,c:None);monkeypatch.setattr(release,'next_release_build',lambda c:1)
    args=Namespace(channel='beta',out=out,sha256=receipt['dmgSha256'],companions_sha256=None)
    with pytest.raises(ValueError,match='approval'):release.publish(args)
    assert calls==[]
    args.companions_sha256=binding['sha256'];release.publish(args)
    assert calls[0][-2:]==('--companions-sha256',binding['sha256'])
    assert json.loads((out/'publication-receipt.json').read_text())['companions']==binding

@pytest.mark.parametrize('corrupt_draft,with_companions',[(False,True),(True,True),(False,False)])
def test_real_publish_shell_checks_companions_before_activation(companions,tmp_path,corrupt_draft,with_companions):
    import os,subprocess,sys
    source,_=manifest(tmp_path);out=tmp_path/'out';out.mkdir();binding=companions.stage(source,out) if with_companions else None
    receipt={'channel':'beta','bundleId':'org.hookeui.HookeUI.beta','version':'0.2.2','notarized':True,'appcast':True,'dmg':'app.dmg','companions':binding}
    (out/'release.json').write_text(json.dumps(receipt));(out/'release-intent.json').write_text(json.dumps({'companions':binding}))
    (out/'app.dmg').write_bytes(b'dmg');(out/'appcast.xml').write_text('<enclosure url="https://github.com/wpulier/hookeui-beta/releases/download/v0.2.2/app.dmg"/>')
    tools=tmp_path/'tools';ff=tools/'ffmpeg/source';ff.mkdir(parents=True)
    for name in ('a.tar.xz','b.tar.gz','c.asc','dBUILD.txt','e.sh'):(ff/name).write_bytes(b'source')
    bin_=tmp_path/'bin';bin_.mkdir();log=tmp_path/'calls'
    program='''import os,sys,json,pathlib,shutil,runpy,urllib.request
name=pathlib.Path(sys.argv[0]).name;a=sys.argv[1:];out=pathlib.Path(os.environ['OUT'])
with open(os.environ['FAKE_LOG'],'a') as f:f.write(json.dumps([name,*a])+'\\n')
if name=='gh':
 if a[:2]==['release','view']:sys.exit(1)
 if a[:2]==['release','download']:
  dest=pathlib.Path(a[a.index('--dir')+1])
  for i,v in enumerate(a):
   if v=='--pattern':
    n=a[i+1];p=out/n if (out/n).exists() else out/'companions'/n
    data=p.read_bytes()
    if os.environ['CORRUPT']=='1' and n=='tool.zip':data=b'corrupt'
    (dest/n).write_bytes(data)
elif name=='swiftc':
 p=pathlib.Path(a[a.index('-o')+1]);p.write_text('#!/bin/sh\\nexit 0\\n');p.chmod(0o755)
elif name=='curl':
 dest=pathlib.Path(a[a.index('-o')+1]);n='appcast.xml' if a[-1].endswith('appcast.xml') else 'app.dmg';shutil.copyfile(out/n,dest)
elif name=='python3':
 if a and a[0].endswith('release_companions.py') and a[1]=='anonymous':
  urllib.request.urlopen=lambda url,timeout:open(out/'companions'/url.rsplit('/',1)[1],'rb')
  sys.argv=a;runpy.run_path(a[0],run_name='__main__')
 else:os.execv(os.environ['REAL_PYTHON'],[os.environ['REAL_PYTHON'],*a])
'''
    for name in ('gh','swiftc','curl','python3'):
        p=bin_/name;p.write_text('#!'+sys.executable+'\n'+program);p.chmod(0o755)
    env={k:v for k,v in os.environ.items() if not k.startswith(('RELEASE','GH_','OUT','TOOLS','ALLOW_','NOTES'))}
    env.update(PATH=str(bin_)+':'+os.environ['PATH'],OUT=str(out),TOOLS=str(tools),FAKE_LOG=str(log),REAL_PYTHON=sys.executable,CORRUPT=str(int(corrupt_draft)))
    result=subprocess.run(['/bin/bash',str(MACOS/'publish.sh'),'--channel','beta',*(['--companions-sha256',binding['sha256']] if binding else [])],env=env,capture_output=True,text=True,timeout=20)
    calls=[json.loads(line) for line in log.read_text().splitlines()]
    activated=any(row[:3]==['gh','release','edit'] for row in calls)
    assert activated is not corrupt_draft,result.stderr
    assert (result.returncode==0) is not corrupt_draft,result.stderr
    create=next(row for row in calls if row[:3]==['gh','release','create'])
    if with_companions:
        assert str(out/'companions/tool.zip') in create and str(out/'companions/companions.json') in create
    else:assert not any('/companions/' in arg for arg in create)
    assert not any('input.json' in arg for arg in create)
    if not corrupt_draft:assert any(row[0]=='python3' and 'anonymous' in row for row in calls)
