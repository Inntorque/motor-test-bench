"""Local feedforward seed plus measured shaft-torque feedback; no hardware I/O.

The reference is conditional on the original speed/history. It initializes a
feedback controller, and is never presented as a universal brake-capacity map.
"""
import math


class BrakeTorque:
    MIN_TARGET_NM = .3  # Stay above the confirmed +/-0.2 N.m mechanical background.
    REFERENCE_MAX_NM = 2.7
    REFERENCE_MAX_CURRENT_A = .4
    MAX_TARGET_NM = 99.9  # Keep the target below the confirmed 100 N.m sensor boundary.
    MAX_CURRENT_A = 1.999  # Preserve the user's strictly-less-than-2 A brake boundary.
    # (whole-revolution shaft mean N.m, SSJ commanded A, source automatic record)
    REFERENCE = (
        (.113077, 0., 'e4eefcefb3994bd89ba0684c339d3245'),
        (.4922, .2, '7fe75212451a4260bd614998d467d43f'),
        (.7739, .25, '9e19f9e67a274f33b7c1b684b71e21d8'),
        (1.3149, .3, '9a06b5f34e1b4583978a8b3f12f21417'),
        (1.3986, .35, '5343b2b59d2243d0938113e476b64838'),
        (2.2483, .375, '67865f53c6aa4a5695d9702ba2567b4b'),
        (2.7863, .4, '4f6bda4e49524494a75540977fe8f673'),
    )

    @classmethod
    def metadata(cls):
        return dict(minTargetNm=0., referenceMinNm=cls.MIN_TARGET_NM, maxTargetNm=cls.MAX_TARGET_NM,
                    maxCurrentA=cls.MAX_CURRENT_A, referenceMaxNm=cls.REFERENCE_MAX_NM,
                    referenceMaxCurrentA=cls.REFERENCE_MAX_CURRENT_A, backgroundNm=.2,
                    date='2026-10-20', feedback='measured_shaft_torque',
                    reference=[dict(torqueNm=t, commandA=a, record=r) for t, a, r in cls.REFERENCE])

    @classmethod
    def feedforward(cls, target):
        if not math.isfinite(target):raise ValueError('目标力矩必须为有限数值')
        if target == 0:return 0.
        if not cls.MIN_TARGET_NM <= target <= cls.REFERENCE_MAX_NM:
            raise ValueError('本轮目标力矩范围为 0.3–2.7 N·m，零目标关闭励磁；未测区间不外推')
        for (t0, a0, _), (t1, a1, _) in zip(cls.REFERENCE, cls.REFERENCE[1:]):
            if t0 <= target <= t1:return a0 + (a1-a0)*(target-t0)/(t1-t0)
        raise ValueError('目标力矩没有实测参考')

    def __init__(self):
        self.target = self.current = self.demand = 0.

    def update(self, target, measured):
        if not math.isfinite(measured):raise ValueError('实测力矩无效')
        if not math.isfinite(target) or not 0<=target<=self.MAX_TARGET_NM:
            raise ValueError('目标力矩需低于 100 N·m 传感器边界')
        # Saturate the seed at the last measured region. Above it, only actual
        # torque error changes current; this does not extrapolate an I/T curve.
        # Below the reliable reference region use measured feedback only.
        # Zero is an OFF baseline, not a demand to cancel bearing torque.
        seed = self.feedforward(min(target,self.REFERENCE_MAX_NM)) if target>=self.MIN_TARGET_NM else 0.
        if target == 0:
            self.target = self.current = self.demand = 0.
            return 0.
        if target != self.target:
            previous_seed=self.feedforward(min(self.target,self.REFERENCE_MAX_NM)) if self.target>=self.MIN_TARGET_NM else 0.
            self.demand += seed-previous_seed
            self.target = target
        error = target-measured
        # Bounded integral trim and slew; no derivative amplification of noise.
        if abs(error) <= .05:
            self.demand = self.current
        else:
            # Discard a pending feedforward jump when feedback has crossed the
            # target; otherwise the slew limiter would keep driving past it.
            if (self.demand-self.current)*error < 0:self.demand = self.current
            self.demand += max(-.01, min(.01, error*.05))
        self.demand = max(0., min(self.MAX_CURRENT_A, self.demand))
        self.current = round(max(0., min(self.MAX_CURRENT_A,
                                       self.current+max(-.025, min(.025, self.demand-self.current)))), 3)
        return self.current
