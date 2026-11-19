"""Prepare a local environment and optional verified portable MongoDB runtime."""
import hashlib
import json
from pathlib import Path
import secrets
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
MONGO_VERSION='8.0.38'
MONGO_URL=f'https://fastdl.mongodb.org/windows/mongodb-windows-x86_64-{MONGO_VERSION}.zip'
MONGO_SHA256='85147ae32f9bb9a2e5a0a3875b0ffcc4423ab7a49bc4f75cb4bbc9811883d3de'

def prepare(download=True):
    runtime=ROOT/'.runtime';runtime.mkdir(exist_ok=True)
    (runtime/'db').mkdir(exist_ok=True)
    env=ROOT/'.env'
    if not env.exists():
        env.write_text('MONGODB_URI=mongodb://127.0.0.1:27018/visadoc_agency\nMONGODB_DATABASE=visadoc_agency\nJWT_SECRET='+secrets.token_urlsafe(48)+'\nJWT_EXPIRES_IN=8h\nMAX_FILE_SIZE=41943040\nLOCAL_SETUP_ENABLED=true\nAI_SERVICE_URL=http://127.0.0.1:8002\nCORS_ORIGINS=http://127.0.0.1:8003,http://localhost:8003,http://127.0.0.1:5173,http://localhost:5173,http://localhost:5174\n',encoding='utf-8')
        print('Created .env with a new random JWT secret; no default administrator credentials.')
    binary=runtime/'mongodb'/'mongod.exe'
    if download and not binary.exists():
        archive=runtime/'mongodb.zip'
        print(f'Downloading MongoDB {MONGO_VERSION} from the official vendor archive…',flush=True)
        urllib.request.urlretrieve(MONGO_URL,archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest()!=MONGO_SHA256:raise RuntimeError('MongoDB archive checksum did not match')
        with zipfile.ZipFile(archive) as z:
            member=next(n for n in z.namelist() if n.endswith('/bin/mongod.exe'))
            binary.parent.mkdir(exist_ok=True);binary.write_bytes(z.read(member))
        (runtime/'mongo-download.json').write_text(json.dumps({'version':MONGO_VERSION,'url':MONGO_URL,'sha256':MONGO_SHA256},indent=2))
        print('Verified portable MongoDB is ready.')
    print('Local setup files are ready. Existing .env was preserved.')

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--external-mongo',action='store_true');args=parser.parse_args();prepare(not args.external_mongo)
