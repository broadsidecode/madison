"""Generate the synthetic sample with a separately installed FFmpeg."""
from pathlib import Path
import argparse
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from timeline_reviewer.prepare import waveform
from timeline_reviewer.manifest import validate_manifest

parser = argparse.ArgumentParser(description='Generate a synthetic demo in a new folder without replacing existing files.')
parser.add_argument('--output', required=True, type=Path, help='New demo bundle directory')
out = parser.parse_args().output.expanduser().resolve()
if out.exists():
    raise SystemExit('Output already exists. Choose a new demo directory.')
out.mkdir(parents=True, exist_ok=False)
(out / 'media').mkdir()
(out / 'thumbs').mkdir()
movie = out / 'media/demo.mp4'
subprocess.run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i',
                'testsrc2=size=640x360:rate=24:duration=30', '-f', 'lavfi', '-i',
                'sine=frequency=220:sample_rate=48000:duration=30',
                '-vf', 'hue=s=0.35:h=60*sin(t/5)',
                '-af', 'volume=0.12,afade=t=in:d=0.1,afade=t=out:st=29.8:d=0.2',
                '-c:v', 'libx264', '-preset', 'medium', '-crf', '28', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-b:a', '96k', '-movflags', '+faststart', str(movie)], check=True)
for name, second in [('poster', 0), ('one', 3), ('two', 13), ('three', 23)]:
    target = out / ('media/poster.jpg' if name == 'poster' else f'thumbs/{name}.jpg')
    subprocess.run(['ffmpeg', '-v', 'error', '-n', '-ss', str(second), '-i', str(movie),
                    '-frames:v', '1', '-vf', 'scale=320:180', '-q:v', '4', str(target)], check=True)

def clip(identifier, label, start, end, **kwargs):
    return {'id': identifier, 'label': label, 'sourceFilename': 'synthetic-demo.mp4',
            'start': start, 'end': end, 'duration': end - start, 'sourceStart': start,
            'sourceEnd': end, 'speed': 1, 'hidden': False, 'volume': 1, **kwargs}

manifest = {'schemaVersion': 1, 'title': 'Community demo', 'duration': 30, 'fps': 24,
            'revision': 'synthetic-demo-v1', 'videoUrl': 'media/demo.mp4', 'posterUrl': 'media/poster.jpg',
            'tracks': [
                {'id': 'picture', 'name': 'Picture', 'kind': 'video', 'clips': [
                    clip('sample-one', 'Sample one', 0, 10, thumbnail='thumbs/one.jpg'),
                    clip('sample-two', 'Sample two', 10, 20, thumbnail='thumbs/two.jpg'),
                    clip('sample-three', 'Sample three', 20, 30, thumbnail='thumbs/three.jpg')]},
                {'id': 'audio', 'name': 'Generated tone', 'kind': 'audio', 'clips': [
                    clip('tone', 'Quiet synthetic tone', 0, 30)]},
                {'id': 'parked', 'name': 'Parked example', 'kind': 'video', 'parked': True,
                 'clips': [clip('alternate', 'Example alternate', 5, 10, hidden=True, thumbnail='thumbs/one.jpg')]}
            ], 'waveform': waveform(movie, 30),
            'notes': ['Synthetic test pattern and quiet generated tone. No production footage is included.',
                      'This viewer displays a rendered movie and its metadata. Review controls do not edit either.']}
(out / 'data.json').write_text(json.dumps(validate_manifest(manifest), indent=2), encoding='utf-8')
print('Synthetic sample generated.')
