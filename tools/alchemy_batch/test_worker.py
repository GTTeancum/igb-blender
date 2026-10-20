import sys
sys.dont_write_bytecode = True
import importlib.util
from pathlib import Path
import tempfile
import unittest
spec=importlib.util.spec_from_file_location('worker',Path(__file__).with_name('worker.py'))
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
class WorkerTests(unittest.TestCase):
 def test_classification(self):
  cases=[(['igGeometryAttr2','igSkeleton','igSkin'],'actors/a.igb','Characters'),(['igGeometryAttr'],'maps/a.igb','Scenes'),(['igAnimation'],'a.igb','Animations'),(['igImage'],'a.igb','UI / Textures'),(['igParticleEmitter'],'a.igb','Effects'),([],'a.igb','Unknown')]
  for names,path,expected in cases:
   with self.subTest(expected): self.assertEqual(w.classify(names,path),expected)
 def settings(self,**kw):return dict(target='wii',texture='wii_rgb5a3',size=256,mipmaps=False,normal=False,specular=False,gloss=False,**kw)
 def test_valid_wii(self):w.validate_settings(self.settings(),'Characters')
 def test_reject_scene_on_wii(self):
  with self.assertRaises(ValueError):w.validate_settings(self.settings(),'Scenes')
 def test_unsupported_combinations(self):
  for key,value in [('target','xbox'),('texture','dxt1'),('size',123),('mipmaps',True),('normal',True)]:
   s=self.settings();s[key]=value
   with self.subTest(key):
    with self.assertRaises(ValueError):w.validate_settings(s,'Characters')
 def test_existing_output_and_source_protected(self):
  root=Path(__file__).resolve().parents[2]/'.local/AlchemyBatchConverter/unit-tests';root.mkdir(parents=True,exist_ok=True)
  with tempfile.TemporaryDirectory(dir=root) as tmp:
   source=Path(tmp)/'source';source.write_bytes(b'original');dest=Path(tmp)/'dest';dest.write_bytes(b'existing')
   with self.assertRaises(ValueError):w.safe_destination(source,source)
   with self.assertRaises(FileExistsError):w.safe_destination(source,dest)
   self.assertEqual(source.read_bytes(),b'original');self.assertEqual(dest.read_bytes(),b'existing')
if __name__=='__main__':unittest.main()
