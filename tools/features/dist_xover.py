"""Distortion crossover: Freq, Mix and Mode on the mono distortions, core and legacy (tools/patch_dist.py,
after every step that adds a model and before the GR knobs; docs/research/sharc/cores/teemah.md, Rollouts D3 to D9). The drive hears the
input above Freq; Mix fades from the clean input (0 %) to the stock block (100 %), equally loud at 50 %, full range (Mode Full) or below Freq (Mode Xover); the defaults (Freq Off) sound like
the stock block. Obsidian 7000 and Minotaur, which mix clean paths of their own, get Freq and Mode only; Kinky Boost and
Vital Boost stay stock (patch_dist.SKIP), and so do the stereo distortions; Xenomorph Fuzz only without NAM A2 Lite
(HX_NAM_SLOTS=0), which takes its place otherwise. Bronze Master and Killer Z get theirs before their hidden Smart.
A checkbox on the web page (WEB 'opt-in': its code is in the blocks, not in Block 3) and HX_DIST_XOVER for build.py. Each
model's DSP cost rises by patch_dist.COST.

Nothing on the M4 side: the knobs live in the blocks (label_color reads HX_DIST_XOVER for the distortions' stock counts)."""
FLAG = 'HX_DIST_XOVER'
WEB = 'opt-in'
