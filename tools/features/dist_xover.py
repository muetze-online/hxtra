"""Distortion crossover: Freq, Dry Level, Wet Level and Mode on the mono distortions, core and legacy (tools/patch_dist.py,
after every step that adds a model and before the GR knobs; docs/research/sharc/cores/teemah.md, Rollouts D3 and D5). The drive hears the input
above Freq; Dry Level mixes in the clean input, full range (Mode Full) or below Freq (Mode Xover); the defaults sound like the
stock block. Obsidian 7000, whose Blend mixes a clean path of its own, gets Freq and Mode only; Clawthorn Drive,
Bitcrusher, Pocket Fuzz, KWB, Swedish Chainsaw, Alpaca Rouge, Ballistic Fuzz, Dark Dove Fuzz, Bighorn Fuzz and Valve Driver stay stock (patch_dist.SKIP), and so do the stereo distortions. Bronze Master and Killer Z
get theirs before their hidden Smart.
A checkbox on the web page (WEB 'opt-in': its code is in the blocks, not in Block 3) and HX_DIST_XOVER for build.py. Each
model's DSP cost rises by patch_dist.COST.

Nothing on the M4 side: the knobs live in the blocks (label_color reads HX_DIST_XOVER for the distortions' stock counts)."""
FLAG = 'HX_DIST_XOVER'
WEB = 'opt-in'
