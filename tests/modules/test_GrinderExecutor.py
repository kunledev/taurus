import os
import shutil

from bzt.modules.grinder import GrinderExecutor, Grinder
from bzt.utils import BetterDict
from tests import setup_test_logging, BZTestCase, __dir__
from tests.mocks import EngineEmul

setup_test_logging()


class TestGrinderExecutor(BZTestCase):
    def test_install_Grinder(self):
        path = os.path.abspath(__dir__() + "/../../build/tmp/grinder-taurus/lib/grinder.jar")
        shutil.rmtree(os.path.dirname(os.path.dirname(path)), ignore_errors=True)

        self.assertFalse(os.path.exists(path))

        obj = GrinderExecutor()
        obj.engine = EngineEmul()
        obj.settings.merge({"path": path})
        obj.execution = BetterDict()
        obj.settings.merge({"properties-file": __dir__() + "/../grinder/grinder.base.properties",
                            "properties": {"sample_prop": "some_val"}})
        obj.execution.merge({"scenario": {
            "script": __dir__() + "/../grinder/helloworld.py",
            "properties-file": __dir__() + "/..//grinder/grinder.properties",
            "properties": {"grinder.useConsole": "false"}}})
        obj.prepare()

        self.assertTrue(os.path.exists(path))

    def test_grinder_widget(self):
        obj = GrinderExecutor()
        obj.engine = EngineEmul()
        obj.engine.config.merge({"provisioning": 'local'})
        obj.execution.merge({"concurrency": {"local": 2},
                             "ramp-up": 2,
                             "hold-for": 2,
                             "scenario": {"script": __dir__() + "/../grinder/helloworld.py"}})
        obj.prepare()
        obj.get_widget()
        self.assertEqual(obj.widget.widgets[0].text, "Script: helloworld.py")

    def test_resource_files_collection_basic(self):
        obj = GrinderExecutor()
        obj.engine = EngineEmul()
        obj.execution.merge({"scenario": {"script": __dir__() + "/../grinder/helloworld.py"}})
        res_files = obj.resource_files()
        self.assertEqual(len(res_files), 1)

    def test_fail_on_zero_results(self):
        obj = GrinderExecutor()
        obj.engine = EngineEmul()
        obj.execution.merge({"concurrency": {"local": 2},
                             "scenario": {"script": __dir__() + "/../grinder/helloworld.py"}})
        obj.prepare()
        self.assertRaises(RuntimeWarning, obj.post_process)

    def test_grinder_mirrors(self):
        path = os.path.abspath(__dir__() + "/../../build/tmp/grinder-taurus/lib/grinder.jar")
        shutil.rmtree(os.path.dirname(os.path.dirname(path)), ignore_errors=True)
        obj = GrinderExecutor()
        grinder_tool = Grinder(path, obj.log, GrinderExecutor.VERSION)
        grinder_tool.install()

    def test_requests(self):
        obj = GrinderExecutor()
        obj.engine = EngineEmul()
        obj.execution.merge({"scenario": {"requests": ['http://blazedemo.com']}})
        obj.prepare()

    def test_full_Grinder(self):
        obj = GrinderExecutor()
        obj.engine = EngineEmul()
        obj.execution.merge({"scenario": {"requests": ['http://blazedemo.com']}})
        obj.prepare()
        # obj.startup()
        # try:
        #     while not obj.check():
        #         time.sleep(obj.engine.check_interval)
        # finally:
        #     obj.shutdown()
