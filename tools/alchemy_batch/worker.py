import sys
sys.dont_write_bytecode = True
"""Blender worker for Alchemy Batch Converter. One conversion per process."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import traceback
import uuid


def emit(kind, **fields):
    print("ABC:" + json.dumps(dict(kind=kind, **fields)), flush=True)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_addon(path):
    spec = importlib.util.spec_from_file_location(
        "igb_blender", Path(path) / "__init__.py",
        submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["igb_blender"] = module
    spec.loader.exec_module(module)
    module.register()


def classify(names, relative):
    """Content first; paths distinguish UI/effects from ordinary geometry."""
    parts = {p.lower() for p in Path(relative).parts[:-1]}
    geometry = any(n.startswith("igGeometryAttr") for n in names)
    if any("Particle" in n or "Emitter" in n for n in names) or "effects" in parts:
        return "Effects"
    if "ui" in parts or "hud" in parts or "textures" in parts:
        return "UI / Textures"
    if geometry and "igSkeleton" in names and "igSkin" in names:
        return "Characters"
    if any(n.startswith(("igAnimation", "igAnimTrack")) for n in names) and not geometry:
        return "Animations"
    if geometry:
        return "Scenes"
    if "igImage" in names:
        return "UI / Textures"
    return "Unknown"


def inspect_asset(path, relative, source_profile="auto"):
    from igb_blender.igb_format.igb_reader import IGBReader
    from igb_blender.game_profiles import detect_profile, get_profile
    reader = IGBReader(str(path))
    reader.read()
    names = [o.type_name.decode("ascii", "replace")
             for o in reader.objects if getattr(o, "type_name", None)]
    category = classify(names, relative)
    profile = (detect_profile(reader) if source_profile == "auto"
               else get_profile(source_profile))
    if profile is None:
        raise ValueError("No matching import profile")
    images = [o for o in reader.objects if getattr(o, "type_name", None) == b"igImage"]
    image_formats = sorted({o.get_slot(11 + reader.slot_offset) for o in images})
    # The upstream decoder currently interprets Wii format 21 as CMPR;
    # do not silently import known RGB5A3 images as corrupted textures.
    limitation = ""
    if 21 in image_formats:
        limitation = "Wii format 21 import needs decoder support; copy or skip"
    if any("Xenon" in n for n in names):
        limitation = "Xbox 360 native geometry is not supported by this importer"
    if category == "Scenes" and any(
            n.startswith(("igAnimation", "igActor", "igParticle", "igEmitter"))
            for n in names):
        limitation = "Scene contains animation/actor/effect objects; copy or skip"
    if category not in ("Characters", "Scenes") and not limitation:
        limitation = "No batch exporter for this asset type; copy or skip"
    mesh_count = sum(n.startswith("igGeometryAttr") for n in names)
    return dict(
        Selected=not limitation, Relative=relative, Type=category,
        Format=profile.game_name + " Â· v" + str(reader.header.version),
        Profile=profile.game_id, Status="Review" if limitation else "Ready",
        Detail=limitation or ("%d geometry objects | %d images | import profile: %s"
                             % (mesh_count, len(images), profile.game_id)),
        Limitation=limitation, Bytes=Path(path).stat().st_size)


def scan(job):
    root = Path(job["source"]).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Source must be a folder")
    output = Path(job["output"]).resolve() if job.get("output") else None
    files = []
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()
                   and not (getattr((Path(base) / d).lstat(), "st_file_attributes", 0) & 0x400)
                   and (output is None or (Path(base) / d).resolve() != output)]
        for name in names:
            path = Path(base) / name
            if path.suffix.lower() == ".igb" and not path.is_symlink():
                files.append(path)
        if not job.get("recursive", True):
            break
    emit("total", total=len(files))
    for index, path in enumerate(sorted(files)):
        relative = str(path.relative_to(root))
        try:
            row = inspect_asset(path, relative, job.get("source_profile", "auto"))
        except Exception as exc:
            row = dict(Selected=False, Relative=relative, Type="Unknown",
                       Format="Unreadable", Status="Error", Detail=str(exc),
                       Limitation=str(exc), Bytes=path.stat().st_size, Profile="")
        emit("asset", asset=row, completed=index + 1, total=len(files))
    emit("done", count=len(files))


def validate_settings(settings, category):
    target = settings["target"]
    rules = {
        "wii": ("wii_rgb5a3",),
        "ps2": ("clut",),
        "gc": ("cmpr",),
        "xml2": ("clut",),
        "mua": ("auto_dxt", "clut"),
    }
    if target not in rules or settings["texture"] not in rules[target]:
        raise ValueError("Unsupported target/texture combination")
    if int(settings["size"]) not in (0, 256, 512, 1024, 2048):
        raise ValueError("Unsupported texture resolution")
    if category == "Scenes" and target not in ("mua", "xml2"):
        raise ValueError("Scene export is currently available for MUA PC / XML2 PC only")
    if category not in ("Characters", "Scenes"):
        raise ValueError("This asset type has no batch conversion path")
    maps = any(settings.get(k, False) for k in ("normal", "specular", "gloss"))
    if maps and (target != "mua" or settings["texture"] != "auto_dxt"):
        raise ValueError("Additional maps are only supported with MUA PC DXT output")
    if target == "mua" and settings["texture"] == "auto_dxt" and not maps:
        raise ValueError("For diffuse-only MUA PC skins, select CLUT (upstream exporter constraint)")
    if settings.get("mipmaps") and target in ("wii", "gc", "ps2", "xml2"):
        raise ValueError("This texture export path produces a base level only")


def safe_destination(source, destination):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    if source == destination:
        raise ValueError("Output would overwrite the source")
    if destination.exists():
        raise FileExistsError("Output already exists; choose a new output folder")
    return source, destination


def convert(job):
    import bpy
    from igb_blender.igb_format.igb_reader import IGBReader
    source, destination = safe_destination(job["source"], job["destination"])
    expected = job.get("source_hash")
    before = digest(source)
    if expected and before != expected:
        raise ValueError("Source changed after the job was prepared")
    action = job["action"]
    if action not in ("convert", "copy"):
        raise ValueError("Unknown operation")
    if action == "convert":
        row = inspect_asset(source, job["relative"], job.get("source_profile", "auto"))
        if row["Limitation"]:
            raise ValueError(row["Limitation"])
        validate_settings(job["settings"], row["Type"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Keep the target basename: the skin exporter derives internal names from it.
    # Each worker's private staging directory is removed after publishing.
    stage = destination.parent / ".alchemy-staging" / uuid.uuid4().hex
    stage.mkdir(parents=True, exist_ok=True)
    temporary = stage / destination.name
    if action == "copy":
        shutil.copyfile(source, temporary)
    else:
        settings = job["settings"]
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        if row["Type"] == "Characters":
            from igb_blender.actor.actor_import import import_actor
            from igb_blender.exporter.skin_export import export_skin
            result = import_actor(
                bpy.context, str(source), skin_filepaths=[("default", str(source))],
                options={"import_animations": False, "import_materials": True,
                         "game_preset": job.get("source_profile", "auto")})
            if not result or not result[1]:
                raise ValueError("No skinned model was imported")
            armature, skins, _ = result
            target = settings["target"]
            texture = "dxt5" if settings["texture"] == "auto_dxt" else settings["texture"]
            scale = armature.get("igb_export_scale", 1.0)
            scaled = (armature.get("igb_converted_rig", False) or
                      (isinstance(scale, (int, float)) and abs(scale - 1.0) > 0.01))
            graph = ("FULL" if scaled else "ANIM") if target == "mua" else "OFF"
            ok = export_skin(
                str(temporary), [(obj, bool(obj.get("igb_is_outline", False))) for _, obj in skins], armature,
                swap_rb=target == "mua", max_texture_size=int(settings["size"]),
                igb_format="V4" if target == "mua" else "V6",
                actor_graph=graph,
                texture_mode=texture,
                use_normal_maps=settings.get("normal", False),
                use_specular_maps=settings.get("specular", False),
                use_gloss_map=settings.get("gloss", False),
                use_mipmaps=settings.get("mipmaps", False))
            if not ok:
                raise ValueError("Skin exporter failed")
        else:
            from types import SimpleNamespace
            from igb_blender.importer.import_igb import import_igb
            from igb_blender.exporter.export_igb import export_igb
            options = SimpleNamespace(
                game_preset=job.get("source_profile", "auto"),
                import_collision=True, import_lights=True, import_entity_models=False,
                collision_source="COLLIDERS", surface_type=0,
                texture_format="clut" if settings["texture"] == "clut" else "dxt5_mua",
                texture_resolution=str(settings["size"]), use_mipmaps=settings["mipmaps"],
                use_normal_maps=settings["normal"], use_specular_maps=settings["specular"],
                use_gloss_map=settings["gloss"], normal_y_flip=True, export_lights=True,
                report=lambda levels, text: emit("message", text=text))
            if import_igb(bpy.context, str(source), options) != {"FINISHED"}:
                raise ValueError("Scene import failed")
            if export_igb(bpy.context, str(temporary), options) != {"FINISHED"}:
                raise ValueError("Scene export failed")
        parsed = IGBReader(str(temporary))
        parsed.read()
        if not any(getattr(o, "type_name", b"").startswith(b"igGeometryAttr")
                   for o in parsed.objects):
            raise ValueError("Export validation failed: no geometry")
    if digest(source) != before:
        raise ValueError("Source changed during processing; output not published")
    output_hash = digest(temporary)
    # Windows rename is atomic on the same volume and refuses an existing file.
    # Cancellation can leave staging data, never a half-written final IGB.
    if os.name == "nt":
        os.rename(temporary, destination)
    else:
        os.link(temporary, destination)
        temporary.unlink()
    if digest(destination) != output_hash:
        destination.unlink()
        raise ValueError("Published output hash mismatch")
    stage.rmdir()
    try:
        stage.parent.rmdir()
    except OSError:
        pass
    emit("result", status="Copied" if action == "copy" else "Converted",
         output=str(destination), sha256=output_hash, source_sha256=before,
         bytes=destination.stat().st_size)


def main():
    job = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8-sig"))
    import bpy
    bpy.context.preferences.filepaths.temporary_directory = job["scratch"]
    load_addon(job["addon"])
    if job["action"] == "scan":
        scan(job)
    else:
        convert(job)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        emit("error", text=str(exc))
        traceback.print_exc()
        sys.exit(1)
