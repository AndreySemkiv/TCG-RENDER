"""Unity clock variables for the renderer's explicitly selected static frame.

This harness chooses t=0 seconds, just as its camera chooses a frontal pose.
It does not claim to capture the game's elapsed time or animated current frame.
The vector formulas are Unity 6000.0 builtins, never guessed material defaults.
"""
import math

REFERENCE='https://docs.unity3d.com/6000.0/Documentation/Manual/SL-UnityShaderVariables.html'
STATIC_FRAME_SECONDS=0.0
CLOCK_VARIABLES=frozenset(('_Time','_SinTime','_CosTime'))


def shader_clock(seconds=STATIC_FRAME_SECONDS):
    seconds=float(seconds)
    if not math.isfinite(seconds) or seconds<0 or not math.isfinite(seconds*3):
        raise ValueError('Shader clock must be finite, nonnegative seconds')
    return {'_Time':[seconds/20,seconds,seconds*2,seconds*3],
            '_SinTime':[math.sin(seconds/d) for d in (8,4,2,1)],
            '_CosTime':[math.cos(seconds/d) for d in (8,4,2,1)]}


def clock_provenance(seconds=STATIC_FRAME_SECONDS):
    return {'authority':'Unity builtin formula with explicit offscreen static clock',
            'seconds':seconds,'reference':REFERENCE,'game_runtime_clock_captured':False,
            'limits':'Exports the selected static frame; it does not reproduce elapsed gameplay time or animation.'}
