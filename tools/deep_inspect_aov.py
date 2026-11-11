import io, zipfile, pathlib, hashlib, re, traceback
import UnityPy

ROOT = pathlib.Path("incoming")
out = []
targets = re.compile(r"(commercializenotify|battleotherui|battlecommon_raw|allshared\.assetbundle|ugui_particles_killnotify|595_lixin_(battle|show)|Actor_595_(Actions|Infos)|595_AssetRef|ResKillBillboardCfg|ResPersonalButtonCfg|resourcepackerinfosetall|resourceverificationinfosetall)", re.I)

def add(s): out.append(str(s))

archives = {}
for p in ROOT.glob("*.zip"):
    archives[p.name] = zipfile.ZipFile(p)
    add(f"\n=== {p.name} ===")
    add(f"sha256={hashlib.sha256(p.read_bytes()).hexdigest()}")

for name, z in archives.items():
    add(f"\n--- TOP LEVEL TARGETS: {name} ---")
    for i in z.infolist():
        if targets.search(i.filename):
            add(f"{i.filename}\tsize={i.file_size}\tcrc={i.CRC:08x}")

    # inspect nested action package
    for i in z.infolist():
        if i.filename.endswith("Actor_595_Actions.pkg.bytes"):
            try:
                raw = z.read(i)
                with zipfile.ZipFile(io.BytesIO(raw)) as nested:
                    add(f"\n[ACTIONS PACKAGE] {i.filename}: {len(nested.infolist())} entries")
                    for ni in nested.infolist():
                        if ni.is_dir(): continue
                        b = nested.read(ni)
                        add(f"{ni.filename}\tsize={len(b)}\tsha256={hashlib.sha256(b).hexdigest()}")
                        if ni.filename.lower().endswith(".xml") and len(b) < 30000:
                            txt=b.decode("utf-8","replace")
                            add("XML_HEAD " + re.sub(r"\s+"," ",txt[:900]))
                        if ni.filename.lower().endswith(".zip"):
                            try:
                                with zipfile.ZipFile(io.BytesIO(b)) as inner:
                                    add(f"  INNER ZIP {ni.filename}: {len(inner.infolist())} entries")
                                    for ii in inner.infolist():
                                        if not ii.is_dir():
                                            add(f"    {ii.filename}\tsize={ii.file_size}\tcrc={ii.CRC:08x}")
                            except Exception as e: add(f"  INNER ZIP ERROR {e}")
            except Exception as e: add(f"ACTIONS ERROR {e}")

    # UnityFS bundle object inventory
    for i in z.infolist():
        if not i.filename.endswith(".assetbundle") or not targets.search(i.filename): continue
        if i.file_size > 25_000_000: 
            add(f"\n[UNITY SKIP LARGE] {i.filename} size={i.file_size}")
            continue
        raw=z.read(i)
        add(f"\n[UNITY BUNDLE] {i.filename} size={len(raw)} head={raw[:20].hex()}")
        try:
            env=UnityPy.load(raw)
            add(f"objects={len(env.objects)}")
            for obj in env.objects:
                try:
                    d=obj.read()
                    typ=obj.type.name
                    nm=getattr(d,"m_Name",None) or getattr(d,"name",None) or ""
                    add(f"  OBJ path_id={obj.path_id} type={typ} name={str(nm)[:180]}")
                    if typ=="TextAsset":
                        script=getattr(d,"m_Script",b"")
                        if isinstance(script,bytes): script=script.decode("utf-8","replace")
                        add("    TEXT " + re.sub(r"\s+"," ",str(script)[:1400]))
                except Exception as e: add(f"  OBJECT ERROR path_id={obj.path_id} {e}")
        except Exception as e:
            add(f"UNITY ERROR {type(e).__name__}: {e}")

pathlib.Path("aov-deep-inspection.txt").write_text("\n".join(out),encoding="utf-8")
print("\n".join(out[:500]))
