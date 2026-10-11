# Copyright (C) 2026 - Joseph M Wilcox
"""Offline new-run estimates from https://developers.tripo3d.ai/en/pricing.

Rates checked 2026-10-06, including the pricing page's P Series table.
Actual API credits_consumed remains authoritative.
"""
from .common import PipelineError

PRICING_DATE = '2026-10-06'
PRICING_URL = 'https://developers.tripo3d.ai/en/pricing'
BASE_CREDITS = {
    'v3.1-20260211': 20,
    'v3.0-20250812': 20,
    'P1-20260311': 40,
    'P2-20260801': 100,
}
TEXTURE_CREDITS = {'fast': 10, 'standard': 10, 'detailed': 20, 'extreme': 30}
RETOPOLOGY_CREDITS = {'v1.0': 10, 'v2.0': 30}
RIG_CREDITS = 25


def estimate(cfg):
    parts = []
    def add(label, minimum, maximum=None):
        parts.append({'label': label, 'min': minimum, 'max': minimum if maximum is None else maximum})
    tripo = cfg['tripo']
    if not cfg['source']:
        model = tripo['model']
        if model not in BASE_CREDITS:
            raise PipelineError(f'No verified price for model {model}.')
        method = 'single-image' if set(cfg['inputs']) == {'image'} else 'multiview'
        add(f'{method} generation ({model})', BASE_CREDITS[model])
        if not model.startswith('P') and tripo['geometry_quality'] == 'detailed':
            add('detailed geometry', 20)
        if tripo['texture']:
            add(f"{tripo['texture_quality']} textures", TEXTURE_CREDITS[tripo['texture_quality']])
        if tripo['quad']:
            add('quad mesh', 5)
    if cfg['retopology']['enabled']:
        model = cfg['retopology']['model']
        add(f'retopology ({model})', RETOPOLOGY_CREDITS[model])
    if cfg['rig']['mode'] != 'none':
        add('rig check', 0)
        if cfg['rig']['mode'] == 'auto':
            add('auto rig (only if riggable)', 0, RIG_CREDITS)
        else:
            add('rigging', RIG_CREDITS)
    return {'parts': parts, 'min': sum(p['min'] for p in parts), 'max': sum(p['max'] for p in parts)}


def credit_range(minimum, maximum):
    return f'{minimum:,}' if minimum == maximum else f'{minimum:,}-{maximum:,}'


def usd_range(minimum, maximum):
    return f'${minimum / 100:,.2f}' if minimum == maximum else f'${minimum / 100:,.2f}-${maximum / 100:,.2f}'


def show_cost(jobs):
    print(f'Estimated cost for {len(jobs)} model(s), full new run:')
    total_min = total_max = 0
    for job in jobs:
        result = estimate(job['config'])
        low, high = result['min'], result['max']
        total_min += low
        total_max += high
        print(f"  {job['name']}: {credit_range(low, high)} credits ({usd_range(low, high)} USD)")
        print(f"    topology: {'quad' if job['config']['tripo']['quad'] else 'triangle'}; face limit: {job['config']['tripo']['face_limit']:,}")
        print(f"    PBR: {'yes' if job['config']['tripo']['pbr'] else 'no'}; de-lighting: {'yes' if job['config']['tripo']['delight'] else 'no'}; output rig: {job['config']['output_rig']['profile']}")
        print(f"    UV unwrapping: {'included (no separate credit charge)' if job['config']['tripo']['export_uv'] else 'omitted'}")
        for part in result['parts']:
            print(f"    {part['label']}: {credit_range(part['min'], part['max'])} credits")
    print(f'Total: {credit_range(total_min, total_max)} credits ({usd_range(total_min, total_max)} USD)')
    print(f'Published rates checked {PRICING_DATE}; 1 credit = $0.01 USD. {PRICING_URL}')
    print('Estimate only; actual billing may differ. Local Blender processing adds no Tripo credits.')
    print('No API requests, builds or files created.')
    return {'min': total_min, 'max': total_max}
