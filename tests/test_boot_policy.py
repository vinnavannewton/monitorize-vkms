import unittest
from monitorize_vkms.boot_policy import validate_transition, validate_protected


def reservation(**changes):
    return dict(schema_version=2, protected=True, owner_kind='remote-login', uid=1000,
                phase='desktop', session='3', boot_id='boot-a', generation='a'*32,
                mode=[1920,1080,60], edid_sha256='b'*64, state='reserved', **changes)


class PolicyTests(unittest.TestCase):
    def change(self, value, operation='prepare', **changes):
        args=dict(uid=1000,session='3',boot_id='boot-a',generation='a'*32)
        args.update(changes)
        return validate_transition(value, operation, **args)

    def test_prepare_idle_and_repeated_idle(self):
        pending=self.change(reservation())
        self.assertEqual(pending['state'],'pending')
        self.assertEqual(reservation()['state'],'reserved')
        idle=self.change(pending,'idle')
        self.assertEqual(idle['state'],'reserved')
        self.assertEqual(self.change(idle,'idle'),idle)

    def test_foreign_owner_session_boot_generation_and_unknown_state_refused(self):
        for values in ({'uid':1001},{'session':'4'},{'boot_id':'old'},{'generation':'c'*32}):
            with self.subTest(values=values), self.assertRaises(PermissionError):
                self.change(reservation(),**values)
        for changes in ({'state':'wrong'},{'uid':True},{'protected':1},
                        {'mode':[2340,1080,60]},{'phase':'wrong'}, {'generation':''}):
            value=reservation();value.update(changes)
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                validate_protected(value)

    def test_activation_requires_exact_connector_identity(self):
        with self.assertRaises(ValueError):
            self.change(reservation(),'activated')
        value=reservation();value.update(connector='Virtual-2',card='card2',connector_id=41)
        self.assertEqual(self.change(value,'activated')['state'],'active')

    def test_release_is_explicit_and_unknown_operations_fail(self):
        self.assertEqual(self.change(reservation(),'release')['state'],'released')
        with self.assertRaises(ValueError):
            self.change(reservation(),'overwrite')
