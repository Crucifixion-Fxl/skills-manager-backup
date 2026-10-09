import importlib.util,json,pathlib,tempfile,unittest
SCRIPT=pathlib.Path(__file__).parents[1]/'scripts/scan_platform_api.py'
spec=importlib.util.spec_from_file_location('scanner',SCRIPT); scanner=importlib.util.module_from_spec(spec);spec.loader.exec_module(scanner)

class ExtractionTests(unittest.TestCase):
    def test_comment_mask_preserves_offsets_and_string_literals(self):
        source = '/* leading\ncomment */ String url = "https://host/path/*literal*/"; // trailing\n'
        masked = scanner.clean(source)
        self.assertEqual(len(masked), len(source))
        self.assertEqual([i for i, c in enumerate(masked) if c == '\n'],
                         [i for i, c in enumerate(source) if c == '\n'])
        self.assertIn('"https://host/path/*literal*/"', masked)
        self.assertNotIn('leading', masked)

    def test_overloaded_handler_provenance_and_effect_use_matched_declaration(self):
        source = '''/* queryRevenueShare is overloaded.
 * A name-only lookup would point to the read overload.
 */
@RestController
@RequestMapping("/devide/cost")
public class Controller {
 @PostMapping("/param/list")
 public Response queryRevenueShare(@RequestBody CostQuery request) {
  return new Response(costService.query(request));
 }
 /* The next overload writes, despite its query name. */
 @PostMapping("/save")
 public Response queryRevenueShare(@RequestBody CostSave request) {
  costService.saveDevideCost(request);
  coefficientService.saveDefaultCoefficient(request);
  return new Response();
 }
}'''
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d)
            (root/'Controller.java').write_text(source)
            routes, unresolved = scanner.java_routes(root)
        self.assertEqual(unresolved, [])
        read, write = routes
        for route, signature in [(read, 'public Response queryRevenueShare(@RequestBody CostQuery'),
                                 (write, 'public Response queryRevenueShare(@RequestBody CostSave')]:
            expected = source[:source.index(signature)].count('\n') + 1
            self.assertEqual(route['source']['line'], expected)
            self.assertTrue(source.splitlines()[expected-1].strip().startswith(signature))
        self.assertEqual(read['path'], '/devide/cost/param/list')
        self.assertEqual(write['path'], '/devide/cost/save')
        self.assertNotEqual(read['source']['line'], write['source']['line'])
        self.assertEqual(read['effect'], 'query-candidate-needs-service-review')
        self.assertEqual(write['effect'], 'mutation-or-unclassified')
        self.assertEqual(write['serviceCalls'], ['coefficientService.saveDefaultCoefficient',
                                                'costService.saveDevideCost'])
        self.assertIn('CostSave', write['requestSignature'])

    def test_echo_group_join_and_commented_routes(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d);(root/'handler').mkdir()
            (root/'handler/handler.go').write_text('''func (h *Handler) Register(v1 *echo.Group) {
 task := v1.Group("/task", jwtMiddleware)
 task.GET("", h.GetTask)
 // task.POST("/deleted", h.Deleted)
 task.POST("/audit", h.Audit)
}
func (h *Handler) Audit(c echo.Context) error {
 if !h.verifyPermissions(c, []string{"admin", "op"}) { return forbidden() }
 return mutate()
}
func (h *Handler) GetTask(c echo.Context) error { return query() }
''')
            result=scanner.go_routes(root)
            self.assertEqual([(r['method'],r['path']) for r in result],[('GET','/api/task'),('POST','/api/task/audit')])
            self.assertIn('"op"',result[1]['permissionEvidence'][0])
    def test_spring_post_query_is_not_assumed_write_and_annotations_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d);(root/'Controller.java').write_text('''@RestController
@RequestMapping("/bill")
public class Controller {
 @ServerAccessible(anyOf = ServerPermissionKeys.BILL)
 @PostMapping("/query")
 public Response<ResultVO> query(@RequestBody QueryDTO request) { return new Response(service.query(request)); }
 @PostMapping("/save")
 public Response save(@RequestBody QueryDTO request) { service.save(request); return new Response(); }
}''')
            result,unresolved=scanner.java_routes(root)
            self.assertEqual(unresolved,[]);self.assertEqual(len(result),2)
            self.assertEqual(result[0]['effect'],'query-candidate-needs-service-review')
            self.assertIn('BILL',''.join(result[0]['permissionEvidence']))
            self.assertEqual(result[1]['effect'],'mutation-or-unclassified')
    def test_test_controllers_are_excluded_from_production_catalog(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d);main=root/'src/main/java';test=root/'src/test/java';main.mkdir(parents=True);test.mkdir(parents=True)
            source='@RestController public class C { @GetMapping("/real") public Response read() { return new Response(); } }'
            (main/'C.java').write_text(source);(test/'Fixture.java').write_text(source.replace('/real','/test-only'))
            result,_=scanner.java_routes(root);self.assertEqual([r['path'] for r in result],['/real'])
    def test_committed_catalogs_keep_deployment_unknown(self):
        for owner in ['delivery/cicd-platform','business-operations/addx-console-admin']:
            path=SCRIPT.parents[3]/owner/'references/source-api-catalog.json'
            catalog=json.loads(path.read_text());self.assertTrue(catalog['routes'])
            self.assertIsNone(catalog['source']['deployedCommit'])
            self.assertTrue(all(r['deployment']=='unknown' for r in catalog['routes']))

# Local safety contract simulation; does not claim to execute the Java/Go server.
def console_authorize(role,annotation,granted):
    if role=='CLIENT':return annotation is not None and (not annotation or bool(set(annotation)&set(granted)))
    if role=='SERVER':return annotation is None or not annotation or bool(set(annotation)&set(granted))
    return False

def apply_guarded_mutation(state,role,required,expected,current_id,authorized_id,effect):
    if role not in required or state['status']!=expected or state['suspended'] or current_id!=authorized_id:
        return False
    effect(); return True

class SafetyContractTests(unittest.TestCase):
    def test_console_unannotated_client_denied_server_retained(self):
        self.assertFalse(console_authorize('CLIENT',None,[]));self.assertTrue(console_authorize('SERVER',None,[]))
        self.assertFalse(console_authorize('SERVER',['BILL'],[]));self.assertTrue(console_authorize('SERVER',['BILL'],['BILL']))
        self.assertFalse(console_authorize('UNKNOWN',[],[]))
    def test_role_state_suspend_and_target_denials_do_not_mutate(self):
        effects=[]
        for role,status,suspended,target in [('rd',3,False,42),('op',2,False,42),('op',3,True,42),('op',3,False,43)]:
            self.assertFalse(apply_guarded_mutation({'status':status,'suspended':suspended},role,['admin','op'],3,target,42,lambda:effects.append('external-deploy')))
        self.assertEqual(effects,[])
        self.assertTrue(apply_guarded_mutation({'status':3,'suspended':False},'op',['admin','op'],3,42,42,lambda:effects.append('external-deploy')))
        self.assertEqual(effects,['external-deploy'])

if __name__=='__main__':unittest.main()
