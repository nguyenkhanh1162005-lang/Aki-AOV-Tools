from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from io import BytesIO
from .merger import merge_zips, MergeError

app = FastAPI(title="Aki AOV Tools", version="0.1.0")
WEB = Path(__file__).resolve().parent.parent / "web"

@app.get("/health")
def health():
    return {"status": "ok", "service": "Aki AOV Tools"}

@app.post("/api/merge")
async def merge(files: list[UploadFile] = File(...)):
    if len(files) > 10:
        raise HTTPException(400, "Tối đa 10 file ZIP mỗi lần.")
    contents = []
    for file in files:
        if not file.filename or not file.filename.lower().endswith(".zip"):
            raise HTTPException(400, "Chỉ chấp nhận file .zip.")
        contents.append(await file.read())
    try:
        result = merge_zips(contents)
    except MergeError as exc:
        raise HTTPException(400, str(exc))
    return StreamingResponse(BytesIO(result), media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="aki-merged.zip"'})

if WEB.exists():
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
