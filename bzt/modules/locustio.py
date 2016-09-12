"""
Module holds all stuff regarding Grinder tool usage

Copyright 2015 BlazeMeter Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import json
import math
import os
import sys
import time
from imp import find_module
from subprocess import STDOUT

from bzt.engine import ScenarioExecutor, FileLister, PythonGenerator
from bzt.modules.aggregator import ConsolidatingAggregator, ResultsProvider, DataPoint, KPISet
from bzt.modules.console import WidgetProvider, ExecutorWidget
from bzt.modules.jmeter import JTLReader
from bzt.six import PY3, iteritems
from bzt.utils import shutdown_process, RequiredTool, BetterDict, dehumanize_time


class LocustIOExecutor(ScenarioExecutor, WidgetProvider, FileLister):
    def __init__(self):
        super(LocustIOExecutor, self).__init__()
        self.script = None
        self.kpi_jtl = None
        self.process = None
        self.__out = None
        self.is_master = False
        self.slaves_ldjson = None
        self.expected_slaves = 0
        self.reader = None
        self.scenario = None
        self.script = None

    def prepare(self):
        self.__check_installed()
        self.scenario = self.get_scenario()
        self.__setup_script()

        self.is_master = self.execution.get("master", self.is_master)
        if self.is_master:
            count_error = ValueError("Slaves count required when starting in master mode")
            slaves = self.execution.get("slaves", count_error)
            self.expected_slaves = int(slaves)

        self.engine.existing_artifact(self.script)

        if self.is_master:
            self.slaves_ldjson = self.engine.create_artifact("locust-slaves", ".ldjson")
            self.reader = SlavesReader(self.slaves_ldjson, self.expected_slaves, self.log)
        else:
            self.kpi_jtl = self.engine.create_artifact("kpi", ".jtl")
            self.reader = JTLReader(self.kpi_jtl, self.log, None)

        if isinstance(self.engine.aggregator, ConsolidatingAggregator):
            self.engine.aggregator.add_underling(self.reader)

    def __check_installed(self):
        tool = LocustIO(self.log)
        if not tool.check_if_installed():
            if PY3:
                raise RuntimeError("LocustIO is not currently compatible with Python 3.x")
            raise RuntimeError("Unable to locate locustio package. Please install it like this: pip install locustio")

    def startup(self):
        self.start_time = time.time()
        load = self.get_load()
        if load.ramp_up:
            hatch = math.ceil(load.concurrency / load.ramp_up)
        else:
            hatch = load.concurrency

        wrapper = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                               os.pardir,
                               "resources",
                               "locustio-taurus-wrapper.py")

        env = BetterDict()
        env.merge({"PYTHONPATH": self.engine.artifacts_dir + os.pathsep + os.getcwd()})
        if os.getenv("PYTHONPATH"):
            env['PYTHONPATH'] = os.getenv("PYTHONPATH") + os.pathsep + env['PYTHONPATH']

        args = [sys.executable, os.path.realpath(wrapper), '-f', os.path.realpath(self.script)]
        args += ['--logfile=%s' % self.engine.create_artifact("locust", ".log")]
        args += ["--no-web", "--only-summary", ]
        args += ["--clients=%d" % load.concurrency, "--hatch-rate=%d" % hatch]
        if load.iterations:
            args.append("--num-request=%d" % load.iterations)

        env['LOCUST_DURATION'] = dehumanize_time(load.duration)
        if self.is_master:
            args.extend(["--master", '--expect-slaves=%s' % self.expected_slaves])
            env["SLAVES_LDJSON"] = self.slaves_ldjson
        else:
            env["JTL"] = self.kpi_jtl

        host = self.get_scenario().get("default-address", None)
        if host:
            args.append("--host=%s" % host)

        self.__out = open(self.engine.create_artifact("locust", ".out"), 'w')
        self.process = self.execute(args, stderr=STDOUT, stdout=self.__out, env=env)

    def get_widget(self):
        """
        Add progress widget to console screen sidebar

        :rtype: ExecutorWidget
        """
        if not self.widget:
            label = "%s" % self
            self.widget = ExecutorWidget(self, "Locust.io: " + label.split('/')[1])
        return self.widget

    def check(self):
        # TODO: when we're in master mode and get no results and exceeded duration - shut down then
        retcode = self.process.poll()
        if retcode is not None:
            self.log.info("Locust exit code: %s", retcode)
            if retcode != 0:
                self.log.warning("Locust exited with non-zero code: %s" % retcode)

            return True

        return False

    def resource_files(self):
        self.__setup_script()
        return [self.script]

    def __tests_from_requests(self):
        filename = self.engine.create_artifact("generated_locust", ".py")
        locust_test = LocustIOScriptBuilder(self.scenario, self.log)
        locust_test.build_source_code()
        locust_test.save(filename)
        return filename

    def __setup_script(self):
        self.script = self.get_script_path()
        if not self.script:
            if "requests" in self.scenario:
                self.script = self.__tests_from_requests()
            else:
                raise ValueError("Nothing to test, no requests were provided in scenario")

    def shutdown(self):
        try:
            shutdown_process(self.process, self.log)
        finally:
            if self.__out:
                self.__out.close()

    def post_process(self):
        no_master_results = (self.is_master and not self.reader.cumulative)
        no_local_results = (not self.is_master and self.reader and not self.reader.buffer)
        if no_master_results or no_local_results:
            raise RuntimeWarning("Empty results, most likely Locust failed")


class LocustIO(RequiredTool):
    def __init__(self, parent_logger):
        super(LocustIO, self).__init__("LocustIO", "")
        self.log = parent_logger.getChild(self.__class__.__name__)

    def check_if_installed(self):
        try:
            find_module("locust")
            self.already_installed = True
        except ImportError:
            self.log.error("LocustIO is not installed, see http://docs.locust.io/en/latest/installation.html")
            return False
        return True

    def install(self):
        raise NotImplementedError("LocustIO auto installation isn't implemented, get it manually")


class SlavesReader(ResultsProvider):
    def __init__(self, filename, num_slaves, parent_logger):
        """
        :type filename: str
        :type num_slaves: int
        :type parent_logger: logging.Logger
        """
        super(SlavesReader, self).__init__()
        self.log = parent_logger.getChild(self.__class__.__name__)
        self.filename = filename
        self.join_buffer = {}
        self.num_slaves = num_slaves
        self.fds = None
        self.read_buffer = ""

    def _calculate_datapoints(self, final_pass=False):
        if not self.fds:
            self.__open_file()

        if self.fds:
            self.read_buffer += self.fds.read(1024 * 1024)
            while "\n" in self.read_buffer:
                _line = self.read_buffer[:self.read_buffer.index("\n") + 1]
                self.read_buffer = self.read_buffer[len(_line):]
                self.fill_join_buffer(json.loads(_line))

        max_full_ts = self.get_max_full_ts()

        if max_full_ts is not None:
            for point in self.merge_datapoints(max_full_ts):
                yield point

    def merge_datapoints(self, max_full_ts):
        for key in sorted(self.join_buffer.keys(), key=int):
            if int(key) <= max_full_ts:
                sec_data = self.join_buffer.pop(key)
                self.log.debug("Processing complete second: %s", key)
                point = DataPoint(int(key))
                for sid, item in iteritems(sec_data):
                    point.merge_point(self.point_from_locust(key, sid, item))
                point.recalculate()
                yield point

    def get_max_full_ts(self):
        max_full_ts = None
        for key in sorted(self.join_buffer.keys(), key=int):
            if len(key) >= self.num_slaves:
                max_full_ts = int(key)
        return max_full_ts

    def __del__(self):
        if self.fds:
            self.fds.close()

    def __open_file(self):
        if os.path.exists(self.filename):
            self.log.debug("Opening %s", self.filename)
            self.fds = open(self.filename, 'rt')
        else:
            self.log.debug("File not exists: %s", self.filename)

    def fill_join_buffer(self, data):
        self.log.debug("Got slave data: %s", data)
        for stats_item in data['stats']:
            for timestamp in stats_item['num_reqs_per_sec'].keys():
                if timestamp not in self.join_buffer:
                    self.join_buffer[timestamp] = {}
                self.join_buffer[timestamp][data['client_id']] = data

    @staticmethod
    def point_from_locust(timestamp, sid, data):
        """
        :type timestamp: str
        :type sid: str
        :type data: dict
        :rtype: DataPoint
        """
        point = DataPoint(int(timestamp))
        point[DataPoint.SOURCE_ID] = sid
        overall = KPISet()
        for item in data['stats']:
            if timestamp not in item['num_reqs_per_sec']:
                continue

            kpiset = KPISet()
            kpiset[KPISet.SAMPLE_COUNT] = item['num_reqs_per_sec'][timestamp]
            kpiset[KPISet.CONCURRENCY] = data['user_count']
            if item['num_requests']:
                avg_rt = (item['total_response_time'] / 1000.0) / item['num_requests']
                kpiset.sum_rt = item['num_reqs_per_sec'][timestamp] * avg_rt
            point[DataPoint.CURRENT][item['name']] = kpiset
            overall.merge_kpis(kpiset)

        point[DataPoint.CURRENT][''] = overall
        point.recalculate()
        return point


class LocustIOScriptBuilder(PythonGenerator):
    IMPORTS = """from locust import HttpLocust, TaskSet, task
from gevent import sleep
import time"""

    def build_source_code(self):
        self.log.debug("Generating Python script for LocustIO")
        header_comment = self.gen_comment("This script was generated by Taurus", "0")
        scenario_class = self.gen_class_definition("UserBehaviour", ["TaskSet"])
        swarm_class = self.gen_class_definition("GeneratedSwarm", ["HttpLocust"])
        imports = self.add_imports()

        self.root.append(header_comment)
        self.root.append(imports)
        self.root.append(scenario_class)
        self.root.append(swarm_class)

        swarm_class.append(self.gen_statement('task_set = UserBehaviour', "4"))

        default_address = self.scenario.get("default-address", "")
        swarm_class.append(self.gen_statement('host = "%s"' % default_address, "4"))

        swarm_class.append(self.gen_statement('min_wait = %s' % 0, "4"))
        swarm_class.append(self.gen_statement('max_wait = %s' % 0, "4"))

        scenario_class.append(self.gen_decorator_statement('task(1)'))
        task = self.gen_method_definition("generated_task", ['self'])
        scenario_class.append(task)

        think_time = dehumanize_time(self.scenario.get('think-time', None))
        for req in self.scenario.get_requests():
            line = 'self.client.%s("%s")' % (req.method.lower(), req.url)
            task.append(self.gen_statement(line))
            if req.think_time:
                task.append(self.gen_statement("sleep(%s)" % dehumanize_time(req.think_time)))
            else:
                if think_time:
                    task.append(self.gen_statement("sleep(%s)" % think_time))

        imports.append(self.gen_new_line(indent="0"))
        scenario_class.append(self.gen_new_line(indent="0"))
        swarm_class.append(self.gen_new_line(indent="0"))
