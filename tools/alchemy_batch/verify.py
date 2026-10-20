"""Private integration checks. Supply an authorized PC actors folder; no game data is tracked."""
import hashlib,json,os,subprocess,sys,time
from pathlib import Path
root=Path(__file__).resolve().parents[2]
app=root/'.local/AlchemyBatchConverter';exe=app/'AlchemyBatchConverter.exe'
run=app/'checks'/time.strftime('%Y%m%d-%H%M%S');run.mkdir(parents=True)
source=run/'input/actors';source.mkdir(parents=True)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
expected=['df2d7f9b197cb8d63fa6b43d0738a06e271456ec80846f0b0d1aa73b56088aa5','e04f0451095585cafac974b34b607c9eed2b09b843999ffef086787ff4dccbd4','78ef64b7a326d1dbe7ed6255339c91e9f3f14793179879bc00d3879456a23de0','413a156727ef5c2217c61ad66996453713a0a5729f0d51b49bf76f96f37b9911']
for i in range(4):(source/f'{90000+i}.igb').write_bytes((Path(sys.argv[1])/f'{15701+i}.igb').read_bytes())
before={p.name:sha(p) for p in source.glob('*.igb')}
checks=[]
def call(mode,src,dest,name,target=None,expected_code=0):
 args=[str(exe),mode,str(src),str(dest),str(run/(name+'.png'))]
 if target:args.append(target)
 result=subprocess.run(args,cwd=root,timeout=180)
 if result.returncode!=expected_code:raise AssertionError((name,result.returncode))
 checks.append(dict(name=name,exit_code=result.returncode))
call('--verify',source.parent,run/'wii','four-model-batch')
for i in range(4):assert sha(run/f'wii/actors/{90000+i}.igb')==expected[i]
call('--verify',source.parent,run/'wii','overwrite-rejected',expected_code=1)
for i in range(4):assert sha(run/f'wii/actors/{90000+i}.igb')==expected[i]
unknown=run/'unknown';unknown.mkdir();(unknown/'unsupported.igb').write_bytes(b'not an IGB')
call('--verify-copy',unknown,run/'copied','copy-unreadable')
assert (run/'copied/unsupported.igb').read_bytes()==b'not an IGB'
for p in source.glob('*.igb'):assert sha(p)==before[p.name]
one=run/'one';one.mkdir();(one/'90000.igb').write_bytes((source/'90000.igb').read_bytes())
for target in ['mua','xml2','ps2','gc']:call('--verify',one,run/target,target,target)
(run/'results.json').write_text(json.dumps({'checks':checks,'exact_game_tested_wii_hashes':expected,'originals_unchanged':True},indent=2))
print(run)
