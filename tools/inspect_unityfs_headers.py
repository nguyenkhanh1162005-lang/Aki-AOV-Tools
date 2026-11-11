import zipfile, pathlib, struct, re, json, hashlib
targets = re.compile(r"(commercializenotify.*595|595_lixin_(battle|show)|battleotherui(_raw)?|allshared\.assetbundle|resourcepackerinfosetall|resourceverificationinfosetall|ugui_particles_killnotify)", re.I)
def cstr(b,p):
    q=b.find(b"\0",p)
    if q<0: return "",p
    return b[p:q].decode("ascii","replace"),q+1
def inspect(name,b):
    p=0
    sig=b[:8]; p=8
    ver=struct.unpack_from(">I",b,p)[0]; p+=4
    unity,p=cstr(b,p); rev,p=cstr(b,p)
    out=[f"{name} bytes={len(b)} sig={sig!r} format={ver} unity={unity!r} revision={rev!r}"]
    if sig!=b"UnityFS\0": return out
    if p+20>len(b): return out+["truncated fixed header"]
    size=struct.unpack_from(">Q",b,p)[0]; p+=8
    csize,usize,flags=struct.unpack_from(">III",b,p); p+=12
    out.append(f"header_end={p} declared_size={size} blockinfo_c={csize} blockinfo_u={usize} flags=0x{flags:08x} low6={flags&0x3f} align200={bool(flags&0x200)} enc400={bool(flags&0x400)} enc1000={bool(flags&0x1000)}")
    out.append("header_hex="+b[:min(160,len(b))].hex())
    # candidate block info offsets
    for off in (p,p+((-p)%16),len(b)-csize,len(b)-csize-((- (len(b)-csize))%16)):
        if off>=0 and off+csize<=len(b):
            out.append(f"candidate_info_offset={off} sample={b[off:off+min(32,csize)].hex()}")
    return out
for zpath in pathlib.Path("incoming").glob("*.zip"):
    print("\nARCHIVE",zpath.name)
    with zipfile.ZipFile(zpath) as z:
        for i in z.infolist():
            if targets.search(i.filename) and i.file_size<25_000_000:
                print("\n".join(inspect(i.filename,z.read(i))))
