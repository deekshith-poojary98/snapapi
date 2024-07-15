from test_parser import TestParser
from api_client import APIClient
from colorama import init, Fore, Style

init(autoreset=True)

class Engine:
    def __init__(self, test_file):
        self.parser = TestParser()
        self.suite = self.parser.parse(test_file)
        self.stop_on_failure = self.suite['options'].get('STOP-ON-FAILURE', True)
        self.failures = []
        self.success = []

    def run(self):
        print(f"\n{'-'*15} SnapAPI Running {'-'*15}")
        print(f"\n{Fore.CYAN}Running test suite:{Fore.RESET} {self.suite['name']}")
        if 'description' in self.suite:
            print(f"{Fore.CYAN}Description:{Fore.RESET} {self.suite['description']}")
        for test in self.suite['tests']:
            if not self.run_test(test['name']) and self.stop_on_failure:
                print(f"\n{Fore.RED}Test suite stopped due to failure in test: {test['name']}")
                print(f"{Fore.GREEN}Note:{Fore.RESET} Set {Fore.BLUE}OPTIONS: {{'STOP-ON-FAILURE': true}}{Fore.RESET} to continue on failure")

                break
        self.print_summary()

    def run_test(self, test_name):
        test = self.suite['test_map'][test_name]
        print(f"\n✔ {Fore.YELLOW} Running test:{Fore.RESET} {test['name']}")
        if 'description' in test:
            print(f"✔ {Fore.YELLOW} Description:{Fore.RESET} {test['description']}")
        if 'tag' in test:
            print(f"✔ {Fore.YELLOW} Tag:{Fore.RESET} {test['tag']}")

        if 'setup' in test:
            if not self.run_test(test['setup']):
                return False

        client = APIClient(test.get('base_url', self.suite.get('base_url')))
        for step in test['steps']:
            if not self.execute_step(client, step, test_name):
                return False

        if 'teardown' in test:
            if not self.run_test(test['teardown']):
                return False
        print(f"✔ {Fore.YELLOW} Status:{Fore.GREEN} PASSED")
        self.success.append(test_name)
        return True

    def execute_step(self, client, step, test_name):
        action = step['action']
        endpoint = step['endpoint']
        data = step.get('data', {})
        try:
            if action == 'GET':
                self.response = client.get(endpoint)
            elif action == 'POST':
                self.response = client.post(endpoint, json=data)
            elif action == 'DELETE':
                self.response = client.delete(endpoint)
            elif action == 'PUT':
                self.response = client.put(endpoint, json=data)
            elif action == 'PATCH':
                self.response = client.patch(endpoint, json=data)

            checks = step.get('checks', [])
            for check in checks:
                self.execute_check(check)
            return True
        except AssertionError as e:
            print(f"❌{Fore.YELLOW} Status:{Fore.RED} FAILED\n{Fore.YELLOW}❌ Reason: {Fore.RED}{e}")
            self.failures.append((test_name, str(e)))
            return False

    def execute_check(self, check):
        check_type = check['type']
        value = check['value']
        if check_type == 'STATUS':
            assert self.response.status_code == int(value), f" Status code expected {value}, got {self.response.status_code}"
        elif check_type == 'CONTAINS':
            assert value in self.response.text, f"Response does not contain {value}"

    def print_summary(self):
        total_tests = len(self.suite['tests'])
        failed_tests = len(self.failures)
        passed_tests = len(self.success)
        print(f"\n{Fore.CYAN}Test Summary: {Fore.MAGENTA}Total:{Fore.RESET} {total_tests}, {Fore.GREEN}Passed:{Fore.RESET} {passed_tests}, {Fore.RED}Failed:{Fore.RESET} {failed_tests}")
        if failed_tests > 0:
            print(f"{Fore.CYAN}Failed tests:")
            for test_name, error in self.failures:
                print(f"{Fore.RED} - {test_name}: {error}")
        print("\n")
