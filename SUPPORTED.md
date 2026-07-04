# IGB Blender Addon — Feature Support Matrix

Last updated: 2026-03-17

## Fully Supported (Import + Export Round-Trip)

| Feature | IGB Type(s) | Import | Export | Notes |
|---------|-------------|--------|--------|-------|
| Mesh geometry | igGeometryAttr1_5, igVertexArray1_1 | ✅ | ✅ | Positions, normals, UVs, vertex colors |
| Multi-material meshes | igAttrSet | ✅ | ✅ | One igAttrSet per material slot |
| Triangle strip conversion | igPrimLengthArray1_1 | ✅ | ✅ | Strip↔list with degenerate separators |
| Materials | igMaterialAttr | ✅ | ✅ | Diffuse, ambient, specular, emission, shininess |
| Diffuse textures | igTextureBindAttr (unit 0) | ✅ | ✅ | DXT3/DXT5/CLUT decode; DXT5/CLUT encode |
| Normal maps | igTextureBindAttr (unit 1) | ✅ | ✅ | Green channel flip (DirectX↔OpenGL) |
| Specular maps | igTextureBindAttr (unit 2) | ✅ | ✅ | Multi-texture pipeline |
| Mipmaps | igImageMipMapList | ✅ | ✅ | Auto-generated on export |
| Lights | igLightAttr | ✅ | ✅ | All 14 properties: type, position, direction, diffuse, ambient, specular, attenuation, falloff, cutoff, shininess, light_id, cast_shadow |
| Light sets | igLightSet, igLightStateSet | ✅ | ✅ | Named light containers with enable/disable state |
| Scene ambient | SceneAmbient igLightSet | ✅ | ✅ | World background ↔ ambient light |
| Collision | igCollideHull | ✅ | ✅ | BVH tree, surface types, Visual/Colliders/None modes |
| Blend state | igBlendStateAttr, igBlendFunctionAttr | ✅ | ✅ | Enabled flag + src/dst/eq/PS2 fields |
| Alpha test | igAlphaStateAttr, igAlphaFunctionAttr | ✅ | ✅ | Enabled + func + ref value |
| Color tint | igColorAttr | ✅ | ✅ | RGBA per-node tint via Multiply node |
| Backface culling | igCullFaceAttr | ✅ | ✅ | Enabled + mode |
| Lighting toggle | igLightingStateAttr | ✅ | ✅ | Per-subtree lighting on/off |
| Texture matrix state | igTextureMatrixStateAttr | ✅ | ✅ | UV animation flag + unit_id |
| PS2 CLUT textures | igClut, igImage (pfmt=65536) | ✅ | ✅ | 256-color palette, universal format |
| MUA PC BGR swap | igImage (DXT with BGR565) | ✅ | ✅ | swap_rb profile flag |
| UV V-flip | — | ✅ | ✅ | DirectX↔OpenGL convention |
| Scene graph hierarchy | igSceneInfo, igGroup, igNode | ✅ | ✅ | Full DAG traversal with instancing on import |
| Bounding boxes | igAABox | ✅ | ✅ | Per-submesh + root union bbox |
| Texture filtering/wrap | igTextureAttr | ✅ | ✅ | mag/min filter, wrap S/T |

## Import Only (No Export)

| Feature | IGB Type(s) | Import | Export | Notes |
|---------|-------------|--------|--------|-------|
| MUA PC meshes | igGeometryAttr2, igVertexArray2 | ✅ | ❌ | Import reads igVertexData components; export writes igGeometryAttr1_5 |
| MUA vertex streams | igVertexData, igVertexStream | ✅ | ❌ | Component-based vertex buffers |
| DXT3 textures | igImage (pfmt=15) | ✅ | ❌ | Decoded on import; export only writes DXT5 or CLUT |
| Wii CMPR textures | igImage (pfmt=21) | ✅ partial | ❌ | Tiled decompression fallback |
| Nested transforms | igTransform | ✅ | ❌ | Import applies matrix chain; export assumes flat scene |
| Multi-root combined maps | multiple igSceneInfo | ✅ | ❌ | Import handles multiple roots; export writes single root |
| Scene graph instancing | DAG node sharing | ✅ | ❌ | Import creates linked copies; export flattens |

## Not Supported

### Scene Graph Nodes

| Type | Description | Priority |
|------|-------------|----------|
| igBillboard | Screen-aligned quads (particles, foliage) | Medium |
| igLod | Level-of-detail switching by distance | Medium |
| igSwitch | Conditional child selection (state-based) | Low |
| igCamera | Camera definitions | Low |
| igVolume | Trigger/collision volumes (AABB) | Medium |
| igCollideHull0 | MUA collision hull variant | Low |

### Rendering State Attributes

| Type | Description | Priority |
|------|-------------|----------|
| igFogAttr / igFogStateAttr | Distance fog | N/A — verified UNUSED by XML1/XML2/MUA maps: in these games "fog" is particle effects via `actionent` + `loopfx` entities (e.g. `map/common/weather_jungle`), not render-state fog |
| igDepthStateAttr | Z-buffer control | Medium |
| igDepthWriteStateAttr | Z-write disable (transparent overlays) | Medium |
| igDepthFunctionAttr | Depth test comparison | Low |
| igStencilStateAttr / igStencilFuncAttr | Stencil buffer effects | Low |
| igColorMaskAttr | Channel write masks | Low |
| igDitherStateAttr | Dithering | Low |
| igNormalizeNormalsStateAttr | Normal renormalization | Low |
| igClippingStateAttr | User clip planes | Low |
| igViewportAttr | Viewport definition | Low |
| igMaterialModeAttr | Material rendering mode | Low |
| igGlobalColorStateAttr | Global color multiplier (MUA) | Low |

### Shaders

| Type | Description | Priority |
|------|-------------|----------|
| igEnvironmentMapShader2 | Reflective surfaces | Medium |
| igBumpMapShader | Engine-level normal/bump mapping | Medium |
| igCartoonShader | Cel/toon shading | Low |
| igDOFShader | Depth of field | Low |
| igInterpretedShader | Custom shader programs (MUA) | Low |
| igMultiTextureShader | Multi-layer shader (MUA) | Low |
| igShaderConstant* | Shader parameters (MUA) | Low |

### Textures

| Type | Description | Priority |
|------|-------------|----------|
| igTextureCubeAttr | Cube map reflections | Low |

### Other

| Type | Description | Priority |
|------|-------------|----------|
| igGraphPath / igGraphPathList | Camera flythrough paths | Low |
| igSceneAmbientColorAttr | Per-scene ambient (alt to SceneAmbient light) | Low |
| igToolInfo | Editor metadata | N/A |

## Map Maker Build Pipeline

| Feature | Status | Notes |
|---------|--------|-------|
| IGB map export | ✅ | Full scene graph build from scratch |
| XMLB compilation | ✅ | .engb, .chrb, .navb, .boyb, .pkgb |
| Entity system (ENGB) | ✅ | 40+ presets, custom properties |
| Collision export | ✅ | BVH tree, surface types, 3 source modes |
| Light export | ✅ | All 14 igLightAttr properties |
| Texture export | ✅ | CLUT, DXT5 XML2, DXT5 MUA |
| Navigation mesh | ✅ | Generated + compiled to .navb |
| Conversation system | ✅ | NPC dialogues via PKGB + Lua |
| Objective system | ✅ | Mission ENGB + zone script |
| Game deployment | ✅ | Auto-copy to game directory |
| Automap (ZAM) | ✅ | v9 XML2, v10 MUA grid format |

## Actor Pipeline

| Feature | Status | Notes |
|---------|--------|-------|
| Skeleton import | ✅ | igSkin, igSkeleton, igJoint |
| Skinning import | ✅ | Blend weights + indices |
| Animation import | ✅ | Enbaya compressed + raw keyframes |
| Skin export | ✅ | Template-based IGB patching |
| Animation export | ✅ | From-scratch IGB builder |
| Rig converter | ✅ partial | VRChat/Unity → XML2 bone mapping |
| VMC motion capture | ✅ | VR → XML2 via proxy armature |

## Other Alchemy Engine Games (Community Tested)

| Game | Platform | Import | Textures | Skeleton | Notes |
|------|----------|--------|----------|----------|-------|
| Digimon World 4 | PS2 | ✅ | ✅ | ✅ | v6 LE, igGeometryAttr1_5, PSMT8 CLUT (77%) + DXT1 (23%), 89/89 files parse, auto-detects as xml1_ps2. 105 game-specific `be*` types safely ignored. |

## Game Profile Auto-Detection

| Profile | Version | Endian | Signature Classes |
|---------|---------|--------|-------------------|
| xml2_pc | 4-7 | LE | igGeometryAttr1_5, igVertexArray1_1 |
| xml2_xbox | 5-7 | LE | igGeometryAttr1_5, igVertexArray1_1 |
| xml1_ps2 | 5-7 | LE | igGeometryAttr1_5, igVertexArray1_1, igClut |
| xml1_xbox | 5-7 | LE | igGeometryAttr1_5, igVertexArray1_1 |
| mua_pc | 8 | LE | igGeometryAttr2, igVertexArray2, igGlobalColorStateAttr |
| mua_xbox360 | 8 | BE | igGeometryAttr2, igVertexArray2 |
| mua_ps3 | 8 | BE | igGeometryAttr1_5, igVertexArray1_1 |
| mua_wii | 8 | BE | igGeometryAttr2, igVertexArray2 |
| mua_psp | 6 | LE | igGeometryAttr2, igVertexArray2 |
| mua_ps2 | 6 | LE | igGeometryAttr1_5, igVertexArray1_1, igClut |
