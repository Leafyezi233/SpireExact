import unittest
from tools.limited_cli import A10_LOGICAL_CPUS, job_memory_mib


def authorized_note(mib='24576'):
    return {'SPIRE_JOB_MEMORY_MIB': mib, 'SPIRE_RESOURCE_OVERRIDE_NOTE': 'User authorized memory relaxation'}


class ScalingLauncherTests(unittest.TestCase):
    def test_existing_job_caps_are_unchanged(self):
        self.assertEqual(job_memory_mib('A0-180s-v1', {}), 6144)
        self.assertEqual(job_memory_mib('A10-seed-v2', {}), 14336)
        self.assertEqual(job_memory_mib('A10-seed-v2-scaling16', {}), 14336)

    def test_scaling_cap_requires_explicit_recorded_authorization(self):
        for env in ({'SPIRE_JOB_MEMORY_MIB': '24576'},
                    {'SPIRE_JOB_MEMORY_MIB': '24576', 'SPIRE_RESOURCE_OVERRIDE_NOTE': ' '}):
            with self.assertRaises(ValueError):
                job_memory_mib('A10-seed-v2-scaling16', env)
        authorized = {'SPIRE_JOB_MEMORY_MIB': '24576', 'SPIRE_RESOURCE_OVERRIDE_NOTE': 'User authorized memory relaxation'}
        self.assertEqual(job_memory_mib('A10-seed-v2-scaling16', authorized), 24576)
        with self.assertRaises(ValueError):
            job_memory_mib('A0-180s-v1', authorized)
        for value in ('0', '-1', '28673'):
            with self.assertRaises(ValueError):
                job_memory_mib('A10-seed-v2', dict(authorized, SPIRE_JOB_MEMORY_MIB=value))

    def test_the_six_core_protocol_enforces_six_cpus_with_a10_memory_rules(self):
        self.assertEqual(A10_LOGICAL_CPUS['A10-seed-v2-cpu6'], 6)
        self.assertEqual(A10_LOGICAL_CPUS['A10-seed-v2'], 8)
        self.assertEqual(A10_LOGICAL_CPUS['A10-seed-v2-scaling16'], 16)
        self.assertEqual(job_memory_mib('A10-seed-v2-cpu6', {}), 14336)
        self.assertEqual(job_memory_mib('A10-seed-v2-cpu6', authorized_note()), 24576)

    def test_an_unregistered_protocol_is_not_treated_as_a10(self):
        with self.assertRaises(ValueError):
            job_memory_mib('A10-seed-v2-cpu5', authorized_note())
