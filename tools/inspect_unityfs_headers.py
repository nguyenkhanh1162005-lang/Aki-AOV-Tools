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

# Deep-decode VTK UnityFS block tables (standard, non-encrypted candidates)
import lzma

def parse_vtk_table(name, b):
    try:
        # Parse the variable-length UnityFS strings before reading the fixed header.
        pos=8
        _version=struct.unpack_from(">I",b,pos)[0]; pos+=4
        _player,pos=cstr(b,pos)
        _engine,pos=cstr(b,pos)
        size=struct.unpack_from(">Q",b,pos)[0]; pos+=8
        csize,usize,flags=struct.unpack_from(">III",b,pos); pos+=12
        pos=(pos+15)&~15
        if flags & 0x80:
            info=b[len(b)-csize:]
        else:
            info=b[pos:pos+csize]
        mode=flags&0x3f
        if mode==1:
            props=info[:5]
            prop=props[0]; lc=prop%9; rest=prop//9; lp=rest%5; pb=rest//5
            dsize=struct.unpack_from("<I",props,1)[0]
            raw=lzma.decompress(info[5:],format=lzma.FORMAT_RAW,filters=[{"id":lzma.FILTER_LZMA1,"dict_size":dsize,"lc":lc,"lp":lp,"pb":pb}])
        elif mode==0: raw=info
        else:
            import lz4.block
            raw=lz4.block.decompress(info,uncompressed_size=usize)
        if len(raw)!=usize: raise ValueError(f"table size {len(raw)} != {usize}")
        q=16
        n=struct.unpack_from(">I",raw,q)[0]; q+=4
        blocks=[]
        for _ in range(n):
            us,cs,bf=struct.unpack_from(">IIH",raw,q); q+=10; blocks.append((us,cs,bf))
        nn=struct.unpack_from(">I",raw,q)[0]; q+=4
        nodes=[]
        for _ in range(nn):
            off,sz,nf=struct.unpack_from(">QQI",raw,q); q+=20
            end=raw.find(b"\0",q)
            path=raw[q:end].decode("utf-8","replace"); q=end+1
            nodes.append((off,sz,nf,path))
        print(f"\\n[DECODED TABLE] {name} flags=0x{flags:x} info_at={pos if not flags&0x80 else len(b)-csize} blocks={blocks} nodes={len(nodes)}")
        for x in nodes[:120]: print("  NODE",x)
    except Exception as e: print(f"\\n[TABLE DECODE ERROR] {name}: {type(e).__name__}: {e}")

for zpath in pathlib.Path("incoming").glob("*.zip"):
    with zipfile.ZipFile(zpath) as z:
        for i in z.infolist():
            if targets.search(i.filename) and i.filename.endswith(".assetbundle") and i.file_size<25_000_000:
                b=z.read(i)
                if b[:8]==b"UnityFS\0": parse_vtk_table(i.filename,b)
