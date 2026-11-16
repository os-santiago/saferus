"""Bounded synthetic console pressure and recovery; not a capacity benchmark."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import ssl
import statistics
import threading
import time
import urllib.error
import urllib.request

from tools.demo import Demo, NoRedirect


EXPECTED = {'verified': True, 'algorithm': 'AES-256-GCM', 'tokenTTLSeconds': 60,
            'tokenRevoked': True, 'agentless': True}


def validate_response(status, raw):
    if len(raw) > 4096:
        raise ValueError('Console response exceeds bound')
    if status == 200:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate result field')
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=unique)
        if value != EXPECTED or any(type(value[k]) is not type(v) for k, v in EXPECTED.items()):
            raise ValueError('Unexpected synthetic verification result')
    elif status != 429 or raw != b'demo busy\n':
        raise ValueError('Unexpected console pressure response')


def request(origin, context, barrier=None):
    # One transport per worker; ambient proxies are excluded and X.509 stays on.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                        urllib.request.HTTPSHandler(context=context))
    req = urllib.request.Request(origin+'/api/demo', data=b'{}', method='POST',
                                 headers={'Content-Type': 'application/json', 'Origin': origin})
    if barrier is not None:
        barrier.wait(timeout=5)
    start = time.monotonic()
    try:
        response = opener.open(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        status, raw = response.status, response.read(4097)
    elapsed = time.monotonic()-start
    validate_response(status, raw)
    return {'status': status, 'milliseconds': round(elapsed*1000, 2)}


def verify(origin, ca):
    context = ssl.create_default_context(cafile=str(ca))
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    if not origin.startswith('https://127.0.0.1:'):
        raise ValueError('Owned loopback port forward required')
    observations = []
    for _ in range(2):
        before = request(origin, context)
        if before['status'] != 200:
            raise ValueError('Console is not ready before bounded burst')
        observations.append(before)
        barrier = threading.Barrier(8)
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(request, origin, context, barrier) for _ in range(8)]
            burst = [future.result(timeout=16) for future in futures]
        if not any(item['status'] == 200 for item in burst):
            raise ValueError('No synthetic verification completed during burst')
        observations.extend(burst)
        recovered = request(origin, context)
        if recovered['status'] != 200:
            raise ValueError('Console did not recover after all burst requests finished')
        observations.append(recovered)
    successes = [item['milliseconds'] for item in observations if item['status'] == 200]
    rejected = sum(item['status'] == 429 for item in observations)
    if rejected == 0:
        raise ValueError('Saturation rejection was not observed; evidence is inconclusive')
    return {'status': 'passed', 'requests': len(observations), 'concurrency': 8, 'bursts': 2,
            'verifiedAndRevoked': len(successes), 'boundedRejections429': rejected,
            'recoveryChecks': 2, 'successfulMilliseconds': {'min': min(successes),
                'median': round(statistics.median(successes), 2), 'max': max(successes)},
            'tls': 'normal X.509 verification with the demo CA; TLS 1.3 minimum',
            'scope': '20 fixed synthetic requests over loopback kubectl port-forward; not a capacity or percentile benchmark',
            'autonomousAISDLCE2E': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path)
    parser.add_argument('--ai-sdlc-state', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--provider', default='podman', choices=['podman', 'docker'])
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise ValueError('New evidence output path required')
    if args.state is None:
        args.state = Path.home()/'.local/state/saferus'/('ai-sdlc-production' if args.ai_sdlc_state else 'demo')
    marker = 'environment-ownership.json' if args.ai_sdlc_state else 'ownership.json'
    if not (args.state/marker).is_file():
        raise ValueError('Existing owned demo required')
    if args.ai_sdlc_state:
        from tools.test_environment import Environment
        demo = Environment(args)
    else:
        demo = Demo(args)
    demo.cluster()
    report = {'status': 'failed', 'autonomousAISDLCE2E': False}
    try:
        from tools.test_environment import Environment
        before = Environment.live_console(demo)
        with demo.api('deployment/saferus-console', 8443) as api:
            report = verify(api.origin.removesuffix('/v1/'), demo.state/'ca.crt')
        if Environment.live_console(demo) != before:
            raise ValueError('Console identity changed during pressure validation')
        report['console'] = before
    except BaseException as error:
        report['status'] = 'failed'
        report['failureType'] = type(error).__name__
        raise
    finally:
        args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with args.output.open('x') as output:
            output.write(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
