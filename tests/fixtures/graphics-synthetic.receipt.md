# Synthetic graphics fixture

`graphics-synthetic.json` uses a generated square on a grid, with neutral copy for all five
graphic components. Generate `data/source/synthetic-grid.mp4` into disposable fixture state with:

```text
ffmpeg -f lavfi -i "color=c=black:s=1080x1920:r=30,drawgrid=w=120:h=120:t=2:c=gray,drawbox=x=400:y=800:w=120:h=120:c=blue:t=fill" -f lavfi -i anullsrc=r=48000:cl=stereo -t 15.2 -c:v libx264 -pix_fmt yuv420p -c:a aac synthetic-grid.mp4
```

The shared fixture validates against the Python model and JSON schema in `tests/test_graphics.py`.
Positions, scale, timing and chart values exercise component layout and duration boundaries.
This receipt describes synthetic input generation; it makes no claim of a rendered export.
