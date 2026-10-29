"""Calculations for the specialized suite; no device access."""
import math
import statistics


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def stats(values):
    values = [v for v in values if finite(v)]
    if not values:
        return {'count': 0}
    return {'count': len(values), 'mean': statistics.fmean(values), 'min': min(values),
            'max': max(values), 'std': statistics.pstdev(values), 'range': max(values)-min(values)}


def fit(points):
    if len(points) < 3:
        return None
    x, y = zip(*points)
    xm, ym = statistics.fmean(x), statistics.fmean(y)
    xx = sum((v-xm)**2 for v in x)
    if xx < 1e-12:
        return None
    slope = sum((a-xm)*(b-ym) for a, b in points)/xx
    intercept = ym-slope*xm
    residual = sum((b-slope*a-intercept)**2 for a, b in points)
    total = sum((b-ym)**2 for b in y)
    return {'slope': slope, 'intercept': intercept, 'rSquared': 1-residual/total if total else None,
            'count': len(points), 'minIqA': min(x), 'maxIqA': max(x)}


def performance(windows, peak, config, issues):
    points = [w for w in windows if w.get('valid')]
    steady = [w for w in points if w.get('steady')]
    best_power = max(steady, key=lambda w: abs(w['powerW']), default=None)
    window_peak = max(points, key=lambda w: abs(w['torque']), default=None)
    highest = max(steady, key=lambda w: abs(w['n']), default=None)
    densities = {}
    for denominator, suffix in [('massKg', 'PerKg'), ('volumeL', 'PerL')]:
        value = config.get(denominator)
        densities['peakTorque'+suffix] = abs(window_peak['torque'])/value if value and window_peak else None
        densities['shortTimePowerW'+suffix] = abs(best_power['powerW'])/value if value and best_power else None
    return {'axis': config.get('axis', 'output'), 'highestObservedRpm': abs(highest['n']) if highest else None,
            'highestSpeedPoint': highest, 'rawTorquePeak': peak, 'windowTorquePeak': window_peak,
            'maxObservedPowerPoint': best_power, 'outputEquivalentKt': None,
            'ktState': 'insufficient_quasistatic_current_levels',
            'ratedSpeedRpm': config.get('ratedSpeedRpm'), 'ratedValueSource': 'operator_nameplate',
            'continuousRating': 'not_certified_by_short_time_sweep', 'densities': densities,
            'validWindows': len(points), 'steadyWindows': len(steady), 'issues': list(issues),
            'currentConvention': 'MBP phase-peak-equivalent Iq; not supply DC current',
            'torqueConvention': 'sensor shaft torque; no assumed gearbox efficiency'}


def positioning(visits, references=None, instrument=None):
    references = references or {}
    instrument = instrument or {}
    aligned = instrument.get('aligned') is True and bool(instrument.get('name'))
    result = {'feedback': [], 'external': [], 'externalReferenceReady': aligned,
              'referenceCount': len(references), 'visitCount': len(visits), 'instrument': instrument}
    for direction in ('forward', 'reverse'):
        targets = sorted({v['nominalDeg'] for v in visits if v['direction'] == direction})
        for target in targets:
            group = [v for v in visits if v['direction'] == direction and v['nominalDeg'] == target]
            errors = [v['meanDeg']-v['targetDeg'] for v in group if finite(v.get('meanDeg'))]
            result['feedback'].append({'targetDeg': target, 'direction': direction, **stats(errors)})
            measured = [references[str(v['visitId'])]-target-instrument.get('zeroDeg', 0)
                        for v in group if str(v['visitId']) in references] if aligned else []
            result['external'].append({'targetDeg': target, 'direction': direction, **stats(measured)})
    for key in ('feedback', 'external'):
        valid = [row for row in result[key] if row['count']]
        result[key+'MaxAbsMeanErrorDeg'] = max((abs(row['mean']) for row in valid), default=None)
        repeated = [row for row in valid if row['count'] >= 2]
        result[key+'WorstRepeatRangeDeg'] = max((row['range'] for row in repeated), default=None)
        result[key+'ReversalDifferenceDeg'] = max((abs(a['mean']-b['mean']) for a in valid for b in valid
                                                  if a['targetDeg'] == b['targetDeg'] and a['direction'] != b['direction']), default=None)
    result['externalCoverageComplete'] = aligned and len(references) == len(visits) and bool(visits)
    result['claim'] = 'external_direct_measurement' if result['externalCoverageComplete'] else 'feedback_only_or_partial_reference'
    return result


def thermal(points, config):
    valid = [p for p in points if finite(p.get('temperatureC'))]
    if not valid:
        return {'count': 0, 'stable': False, 'measuringPoint': 'existing motor NTC'}
    latest = valid[-1]
    window = config.get('thermalWindowS', 600)
    tail = [p for p in valid if p['t'] >= latest['t']-window and p.get('phase') != 'cooling']
    span = tail[-1]['t']-tail[0]['t'] if tail else 0
    slope = None
    if span >= window*.95 and len(tail) >= 3:
        tm = statistics.fmean(p['t'] for p in tail)
        cm = statistics.fmean(p['temperatureC'] for p in tail)
        denominator = sum((p['t']-tm)**2 for p in tail)
        slope = sum((p['t']-tm)*(p['temperatureC']-cm) for p in tail)/denominator*60 if denominator else None
    # Stability is evaluated only while the declared operating point stays similar.
    operating = True
    for field, floor in [('iq', .1), ('speedRpm', 1), ('torqueNm', .05)]:
        values = stats([p.get(field) for p in tail])
        if values.get('count', 0) < 2 or values.get('count',0)!=len(tail) or values['range'] > max(floor, abs(values['mean'])*.1):
            operating = False
    stable = slope is not None and abs(slope) <= config.get('thermalSlopeCPerMin', .1) and operating
    ambient = config.get('ambientC')
    return {'count': len(valid), 'initialC': valid[0]['temperatureC'], 'lastC': latest['temperatureC'],
            'maxC': max(p['temperatureC'] for p in valid),
            'riseK': latest['temperatureC']-ambient if finite(ambient) else None,
            'changeFromInitialK': latest['temperatureC']-valid[0]['temperatureC'],
            'slopeCPerMin': slope, 'stable': stable, 'operatingPointStable': operating,
            'windowS': window, 'measuringPoint': 'existing motor NTC; not assumed winding temperature'}
