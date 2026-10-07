"""KernelSentinel source-audited observation annotations; no overlap invention."""
from ..kernel_sentinel_rules import classify, VERSION
from .contract import PolicyAnnotations


def evaluate(event, baseline):
    if event['module'] != 'kernel_sentinel' or baseline.module != event['module']:
        raise ValueError('KernelSentinel policy supports only kernel_sentinel')
    result = classify(event['raw_score'], event['evidence'], event['reasons'])
    return PolicyAnnotations(notes=(
        f'{VERSION}: source-derived provisional classification={result}; not replay-calibrated.',
        'Raw 0..4 is preserved; cycle max and latest snapshot are not summed across samples.',
        'Valid raw 3 integrity changes / raw 4 configured hash matches activate provisional threshold 3.',
        'Access identity/scope uncertainty remains pending; weak name/probe signals are advisory.',
        'Load-time/first-scan baselines are not trusted-clean; hash presence is not proof of cheat activation.',
        'ERROR or measurement_valid=false is unavailable, not normal zero; raw positive evidence is retained.',
        'No cross-detector overlap tag: temporal proximity alone does not establish a shared incident.',
    ))
