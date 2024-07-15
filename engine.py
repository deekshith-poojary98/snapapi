from test_parser import TestParser
from api_client import APIClient
from colorama import init, Fore, Style

# TODO: Code optimization required

init(autoreset=True)

class Engine:
    def __init__(self, test_file):
        self.parser = TestParser()
        self.suite = self.parser.parse(test_file)
        self.stop_on_failure = self.suite['options'].get('STOP-ON-FAILURE', True)
        self.failures = []
        self.success = []
        self.is_setup = False

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

    def print_setup_info(self, setup_message, test_message, test_case):
        if self.is_setup:
            print(f"{Fore.YELLOW} {setup_message}:{Fore.RESET} {test_case}")
        else:
            print(f"{Fore.YELLOW}{test_message}:{Fore.RESET} {test_case}")


    def run_test(self, test_name):
        test = self.suite['test_map'][test_name]

        self.print_setup_info(" ✔  Running setup", "\n✔  Running test", test['name'])
        if 'description' in test:
            self.print_setup_info(" ✔  Setup description", "✔  Test description", test['description'])
        if 'tag' in test:
            self.print_setup_info(" ✔  Setup tag", "✔  Test tag", test['tag'])

        if 'setup' in test:
            self.is_setup = True
            if not self.run_test(test['setup']):
                return False

        client = APIClient(test.get('base_url', self.suite.get('base_url')))
        for step in test['steps']:
            if not self.execute_step(client, step, test_name):
                return False

        if 'teardown' in test:
            if not self.run_test(test['teardown']):
                return False

        if self.is_setup:
            print(f"  ✔ {Fore.YELLOW} Setup status:{Fore.GREEN} PASSED")
        else:
            if self.is_setup:
                print(f"\n✔ {Fore.YELLOW} Test status:{Fore.GREEN} PASSED")
            else:
                print(f"✔ {Fore.YELLOW} Test status:{Fore.GREEN} PASSED")
            self.success.append(test_name)
            print(f"\n{'- '*20}")
        self.is_setup = False
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
            if self.is_setup:
                print(f" ❌{Fore.YELLOW} Setup status:{Fore.RED} FAILED\n{Fore.YELLOW} ❌ Reason: {Fore.RED}{e}")
            else:
                print(f"❌{Fore.YELLOW} Test Status:{Fore.RED} FAILED\n{Fore.YELLOW}❌ Reason: {Fore.RED}{e}")
            print(f"\n{'- '*20}")
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
