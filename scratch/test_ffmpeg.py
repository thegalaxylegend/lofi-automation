from pathlib import Path
import subprocess
import os

os.chdir(Path(__file__).parent.parent)

TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

# Create dummy inputs
for i in range(10):
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=192x108", "-vframes", "1", f"temp/bg_img_{i:02d}.jpg"], capture_output=True)
subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=f=440:d=10", "temp/audio.mp3"], capture_output=True)

import urllib.request
font_path = TEMP_DIR / "Roboto-Regular.ttf"
if not font_path.exists():
    urllib.request.urlretrieve("https://github.com/googlefonts/roboto/raw/main/src/hinted/Roboto-Regular.ttf", font_path)

safe_font_path = str(font_path.absolute()).replace("\\", "/").replace(":", "\\:")

channel_txt_path = TEMP_DIR / "channel.txt"
with open(channel_txt_path, "w") as f:
    f.write("Lofi Channel")
safe_channel_path = str(channel_txt_path.absolute()).replace("\\", "/").replace(":", "\\:")

suggestion_txt_path = TEMP_DIR / "suggestion.txt"
with open(suggestion_txt_path, "w") as f:
    f.write("Relaxing beats")
safe_text_path = str(suggestion_txt_path.absolute()).replace("\\", "/").replace(":", "\\:")

filters = []
w, h = 1920, 1080
fps = 30
audio_duration = 10.0
concat_inputs = ""
for i in range(10):
    filters.append(f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},zoompan=z='zoom+0.0005':d=30:s={w}x{h}:fps={fps}[v{i}]")
    concat_inputs += f"[v{i}]"

filters.append(f"{concat_inputs}concat=n=10:v=1:a=0[slideshow]")
filters.append(f"[slideshow]eq=brightness=-0.05:contrast=1.1:saturation=0.7,hue=s=0[graded]")
filters.append(f"[graded]noise=c0s=12:c0f=t+u:allf=t+u[grained]")

audio_idx = 10
viz_h = 100
filters.append(f"[{audio_idx}:a]showcqt=s={w}x{viz_h}:count=6:fcount=2:sono_h=0:bar_h=1:sono_g=4:bar_g=2:fontfile='{safe_font_path}':fontcolor='0x6C3CE1':tc=0.33:tlength=2[viz]")
filters.append(f"[viz]format=rgba,colorchannelmixer=aa=0.6[vizt]")
filters.append(f"[grained][vizt]overlay=0:960:shortest=1[withviz]")

filters.append(f"[withviz]drawtext=textfile='{safe_channel_path}':fontsize=24:fontcolor=white@0.7:x=w-tw-30:y=h-th-30:fontfile='{safe_font_path}'[withtext]")

enable_expr = f"between(t\\,3\\,{audio_duration - 2})"
filters.append(f"[withtext]drawtext=textfile='{safe_text_path}':fontsize=28:fontcolor=white@0.8:x=(w-tw)/2:y=h*0.15:fontfile='{safe_font_path}':enable='{enable_expr}'[final]")

filter_complex = ";\n".join(filters)

filter_script_path = TEMP_DIR / "filters.txt"
with open(filter_script_path, "w") as f:
    f.write(filter_complex)

cmd = ["ffmpeg", "-y"]
for i in range(10):
    cmd.extend(["-loop", "1", "-t", "1.0", "-i", f"temp/bg_img_{i:02d}.jpg"])
cmd.extend(["-i", "temp/audio.mp3"])
cmd.extend(["-filter_script", str(filter_script_path), "-map", "[final]", "-map", "10:a", "-t", "2", "temp/out.mp4"])

print(f"Filter Script:\n{filter_complex}\n")
res = subprocess.run(cmd, capture_output=True, text=True)
print("Return code:", res.returncode)
for line in res.stderr.splitlines():
    if "Parse error" in line or "Error" in line or "error" in line or "Invalid" in line:
        print(line)
