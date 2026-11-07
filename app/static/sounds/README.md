# Notification sounds

The console's ten sounds (bell, ding-dong, marimba, drop, chimes, bubble, arpeggio, small-bell, xylophone, chord) are synthesised by [app/tools/make_sounds.py](../../tools/make_sounds.py): sine waves and envelopes, with no recording or sample from a third party.

They are dedicated to the public domain (CC0 1.0): anyone may use, modify and redistribute them without attribution.

To make them again: `python3 app/tools/make_sounds.py`. It needs numpy, and ffmpeg with libmp3lame.
