"""Obtain the pinned Windows media tools directly from their upstream distributor."""
import hashlib
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen

RELEASE = '9.1'
URL = f'https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-{RELEASE}-essentials_build.zip'
SHA256 = 'b9bbbb3acece000cbeec994f856649354e52c773807841e78908374b24d658fd'
FILES = ('ffmpeg.exe', 'ffprobe.exe')


def media_tools():
    directory = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'XingmuStudio' / f'ffmpeg-{RELEASE}'
    if all((directory / name).is_file() for name in FILES):
        return directory
    print('Installing video tools for the first launch. Please wait...', flush=True)
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=directory) as temp:
        work = Path(temp)
        archive_path = work / 'ffmpeg.zip'
        digest = hashlib.sha256()
        total = 0
        with urlopen(URL, timeout=45) as response, archive_path.open('wb') as destination:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 180 * 1024 * 1024:
                    raise ValueError('Video tools download exceeds the expected size.')
                digest.update(chunk)
                destination.write(chunk)
        if digest.hexdigest() != SHA256:
            raise ValueError('Video tools checksum did not match.')
        with zipfile.ZipFile(archive_path) as archive:
            for name in FILES:
                member = f'ffmpeg-{RELEASE}-essentials_build/bin/{name}'
                with archive.open(member) as source, (work / name).open('wb') as destination:
                    shutil.copyfileobj(source, destination)
        for name in FILES:
            (work / name).replace(directory / name)
    return directory
