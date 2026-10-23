"""Offline checks only: no device or service access."""
import unittest

from coupled_current import StandstillConfirmation


class StandstillTests(unittest.TestCase):
    def sample(self, detector, t, *, rpm=0, position=10, stamp=None, sensor=0, stale=False):
        stamp=t if stamp is None else stamp
        motor={'snapshot':{
            'motor':{'state':2,'output_on':True,'updated_at':t},
            'encoder':{'valid':True,'stale':False},
            'feedback':{'speed_rpm':rpm,'position_deg':position,'updated_at':stamp,
                        'position_at':stamp,'stale':stale,'position_stale':False}}}
        return detector.update({'speed':sensor,'speedDecimals':1},motor,eligible=True,now=t)

    def test_sensor_zero_and_086_rotor_rpm_never_confirms(self):
        detector=StandstillConfirmation()
        for t in (100,100.8,101.6,102.4):
            self.assertFalse(self.sample(detector,t,rpm=.86))
        self.assertFalse(detector.pending)

    def test_fresh_stationary_encoder_requires_distinct_samples_and_duration(self):
        detector=StandstillConfirmation()
        self.assertFalse(self.sample(detector,100))
        self.assertFalse(self.sample(detector,100.8))
        self.assertTrue(self.sample(detector,101.6))
        self.assertTrue(detector.evidence['confirmed'])

    def test_repeated_or_stale_position_cannot_count_as_stationary_time(self):
        detector=StandstillConfirmation()
        for t in (100,100.5,101,102):
            self.assertFalse(self.sample(detector,t,stamp=100))
        self.assertFalse(detector.pending)
        self.assertFalse(self.sample(detector,103,stale=True))

    def test_position_motion_cannot_be_hidden_by_zero_speed_estimate(self):
        detector=StandstillConfirmation()
        for i in range(10):
            self.assertFalse(self.sample(detector,100+i*.3,position=10+i))

    def test_one_display_count_is_not_a_zero_reading(self):
        detector=StandstillConfirmation()
        for t in (100,100.8,101.6):
            self.assertFalse(self.sample(detector,t,sensor=.1))
        self.assertFalse(detector.pending)


if __name__=='__main__':
    unittest.main()
