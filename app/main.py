from fastapi import FastAPI

app = FastAPI(title="Uber Backend")

@app.get("/")
def root():
    return {"message": "Backend running"}

@app.get("/health")
def health():
    return {"status": "ok"}