"""Build the Windows app with installed tools, without downloads or package restore."""
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile

BACKEND_COMMIT = "4672c8d79a4539333632d1ada70937672e1c21ae"
root = Path(__file__).resolve().parents[2]
src = root / "tools/alchemy_batch"
out = root / ".local/AlchemyBatchConverter"
build = out / "build"
build.mkdir(parents=True, exist_ok=True)
env = dict(os.environ, TEMP=str(build), TMP=str(build), DOTNET_CLI_HOME=str(build),
           DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1", DOTNET_NOLOGO="1",
           PYTHONDONTWRITEBYTECODE="1")
# Archive an immutable backend revision, not the app or private working files.
subprocess.run(["git", "archive", "--format=zip", f"--output={build / 'addon.zip'}",
                BACKEND_COMMIT], cwd=root, check=True)
with zipfile.ZipFile(build / "addon.zip") as bundle:
    profile_source = bundle.read("game_profiles.py").decode("utf-8")
profiles = {"auto": "Auto-detect (review detected profile)"}
profiles.update(re.findall(r'game_id="([^"]+)",\s*game_name="([^"]+)"', profile_source))
(build / "profiles.json").write_text(json.dumps(profiles), encoding="utf-8")
sdks = subprocess.check_output(["dotnet", "--list-sdks"], env=env, text=True)
installed = []
for line in sdks.splitlines():
    match = re.fullmatch(r'(\d+\.\d+\.\d+) \[(.+)\]', line)
    if match:
        installed.append((tuple(map(int, match[1].split('.'))), Path(match[2]) / match[1]))
if not installed:
    raise SystemExit("Install a stable .NET SDK containing Roslyn before building.")
csc = sorted(installed)[-1][1] / "Roslyn/bincore/csc.dll"
ref = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / r"Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8.1"
if not (ref / "PresentationFramework.dll").exists():
    raise SystemExit("The .NET Framework 4.8.1 developer targeting pack is required.")
refs = ["mscorlib", "System", "System.Core", "System.Xaml", "System.Web.Extensions",
        "System.Windows.Forms", "System.Drawing", "WindowsBase", "PresentationCore",
        "PresentationFramework", "System.IO.Compression", "System.IO.Compression.FileSystem"]
args = ["dotnet", str(csc), "/nologo", "/target:winexe", "/platform:x64", "/optimize+",
        "/langversion:latest", "/nostdlib+", f"/out:{out / 'AlchemyBatchConverter.exe'}"]
args += [f"/reference:{ref / (name + '.dll')}" for name in refs]
args += [f"/resource:{path},{name}" for path, name in [
    (src / "Main.xaml", "Main.xaml"), (src / "worker.py", "worker.py"),
    (build / "profiles.json", "profiles.json"), (build / "addon.zip", "addon.zip")]]
args.append(str(src / "App.cs"))
subprocess.run(args, cwd=root, env=env, check=True)
(out / "BUILD.json").write_text(json.dumps({"app_version": "0.1.0", "backend_commit": BACKEND_COMMIT,
    "import_profiles": len(profiles)-1, "runtime": ".NET Framework 4.8.1", "blender": "4.5"}, indent=2))
print(out / "AlchemyBatchConverter.exe")
