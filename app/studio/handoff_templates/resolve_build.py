"""Build this episode's timeline in DaVinci Resolve Studio.

1. Open DaVinci Resolve Studio.
   Preferences > System > General > "External scripting using" = Local.
2. Optional: run render_bumpers.bat first so the Blender bumpers are used.
3. From this folder:   python resolve_build.py
   (or Workspace > Scripts after copying this folder into Resolve's Scripts dir)

It creates a project + 1920x1080/30fps timeline laid out in the DanDon format:
  V1  layout plate (links bar, host/logo panel, SOURCES link)  — full length
  V2  fact visuals: real document frames, scaled into the 3/4 main area
  V3  animated connective tissue (Blender bumper if rendered, else keyframe)
  A1  narration        A2  music bed
  Markers: blue = segment starts, red = a shot that still needs a frame grab.
Everything lands on normal tracks, so finish the cut by hand as usual.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
PLAN = json.load(open(os.path.join(HERE, "plan.json"), encoding="utf-8"))
FPS = PLAN["fps"]

# Where the 3/4 main area sits on a 1920x1080 frame (tweak if you move the layout).
MAIN_ZOOM = 0.75      # 1440 / 1920
MAIN_PAN = -240       # centre of main area is 240 px left of frame centre
MAIN_TILT = 135       # ...and 135 px above it


def get_resolve():
    if "resolve" in globals():
        return globals()["resolve"]
    try:
        import DaVinciResolveScript as dvr  # noqa: N813
    except ImportError:
        for base in (r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\Modules",
                     "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules",
                     "/opt/resolve/Developer/Scripting/Modules"):
            if os.path.isdir(base):
                sys.path.append(base)
        import DaVinciResolveScript as dvr  # noqa: N813
    r = dvr.scriptapp("Resolve")
    if r is None:
        sys.exit("Can't reach Resolve — is it open with external scripting set to Local?")
    return r


def f(seconds):
    return int(round(seconds * FPS))


def main():
    resolve = get_resolve()
    pm = resolve.GetProjectManager()
    project = pm.CreateProject(PLAN["project_name"]) or pm.LoadProject(PLAN["project_name"]) \
        or pm.GetCurrentProject()
    for key, val in (("timelineResolutionWidth", "1920"), ("timelineResolutionHeight", "1080"),
                     ("timelineFrameRate", str(FPS)), ("timelinePlaybackFrameRate", str(FPS))):
        project.SetSetting(key, val)

    pool = project.GetMediaPool()
    folder = pool.AddSubFolder(pool.GetRootFolder(), PLAN["episode_title"][:60]) or pool.GetRootFolder()
    pool.SetCurrentFolder(folder)

    def media(rel):
        # Prefer the Blender render of a bumper when it exists.
        alt = rel.replace(".mp4", "_blender.mp4")
        use = alt if os.path.exists(os.path.join(HERE, alt)) else rel
        return os.path.join(HERE, use)

    wanted = sorted({media(c["file"]) for c in PLAN["clips"]})
    imported = pool.ImportMedia([p for p in wanted if os.path.exists(p)]) or []
    items = {}
    for item in imported:
        items[os.path.normcase(item.GetClipProperty("File Path"))] = item

    timeline = pool.CreateEmptyTimeline(PLAN["timeline_name"])
    project.SetCurrentTimeline(timeline)
    while timeline.GetTrackCount("video") < 3:
        timeline.AddTrack("video")
    while timeline.GetTrackCount("audio") < 2:
        timeline.AddTrack("audio", "stereo")
    for i, name in enumerate(["Layout plate", "Fact visuals", "Connective tissue"], start=1):
        timeline.SetTrackName("video", i, name)
    timeline.SetTrackName("audio", 1, "Narration")
    timeline.SetTrackName("audio", 2, "Music bed")
    start = timeline.GetStartFrame()

    placed, missing = 0, []
    for clip in PLAN["clips"]:
        item = items.get(os.path.normcase(media(clip["file"])))
        if item is None:
            missing.append(clip["file"])
            continue
        length = max(f(clip["end"]) - f(clip["start"]), 1)
        info = {"mediaPoolItem": item, "startFrame": 0, "endFrame": length - 1,
                "trackIndex": clip["track"], "recordFrame": start + f(clip["start"]),
                "mediaType": 2 if clip["type"] == "audio" else 1}
        result = pool.AppendToTimeline([info])
        if not result:
            missing.append(clip["file"])
            continue
        placed += 1
        tl_item = result[0]
        if clip.get("main_area"):
            for prop, val in (("ZoomX", MAIN_ZOOM), ("ZoomY", MAIN_ZOOM), ("Pan", MAIN_PAN), ("Tilt", MAIN_TILT)):
                tl_item.SetProperty(prop, val)
        if clip.get("volume_db") is not None:
            try:
                tl_item.SetProperty("Volume", clip["volume_db"])
            except Exception:
                pass

    for m in PLAN["markers"]:
        timeline.AddMarker(f(m["at"]), m["color"], m["name"][:60], m.get("note", ""), 1)

    print(f"Timeline '{PLAN['timeline_name']}': {placed} clips placed, {len(PLAN['markers'])} markers.")
    if missing:
        print("Not placed (check these files):", *missing, sep="\n  ")


if __name__ == "__main__" or "resolve" in globals():
    main()
