"""The runtime exports a hostd DTO independent of legacy import isolation."""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from hostd import onboarding as ob
import test_hostd_onboarding_runtime as fixture

class Bindings(unittest.TestCase):
    def test_owned_domain_snapshot_is_immutable_and_strictly_validated(self):
        original={'a'*64:'on_owner'}
        answer=ob.PeopleBindings(original)
        original.clear()
        projection=answer.union_ids;projection.clear()
        self.assertEqual(ob.IdentityResolver._validate_people(answer),{'a'*64:'on_owner'})
        self.assertNotIn('on_owner',repr(answer))
        for value in ('PRIVATE',{'wrong':'on_owner'},{'a'*64:'ou_owner'},{'a'*64:'on_owner','b'*64:'on_owner'}):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(ValueError) as caught:ob.PeopleBindings(value)
                self.assertIn('怎么解决',str(caught.exception))
        with self.assertRaises(ValueError):ob.IdentityResolver._validate_people(SimpleNamespace(union_ids={'a'*64:'on_owner'}))

@unittest.skipUnless(fixture.fixture.AESGCM is not None,'protected runtime requires cryptography')
class RuntimeBindings(unittest.IsolatedAsyncioTestCase):
    setUp=fixture.RuntimeTests.setUp
    create=fixture.RuntimeTests.create
    bound=fixture.RuntimeTests.bound
    async def test_actual_runtime_returns_the_consumers_domain_bindings(self):
        self.bound();service=await self.create()
        answer=await service._people_loader('cli_agent',self.w.now)
        self.assertIsInstance(answer,ob.PeopleBindings)
        person=await service.identity.resolve('cli_agent',ob.Operator('ou_owner','on_owner'),now=self.w.now)
        self.assertEqual(person.pubkey,self.f.owner)

if __name__=='__main__':unittest.main()
