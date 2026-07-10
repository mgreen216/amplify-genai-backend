#!/usr/bin/env python3
"""
Performance Monitoring Script for Amplify Platform
Measures and reports on key performance metrics
"""

import time
import requests
import statistics
import concurrent.futures
import json
import boto3
from datetime import datetime
from typing import Dict, List, Tuple

class PerformanceMonitor:
    def __init__(self, base_url: str, api_key: str = None):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.cloudwatch = boto3.client('cloudwatch')
        self.results = {
            'timestamp': datetime.utcnow().isoformat(),
            'tests': []
        }
    
    def measure_endpoint(self, endpoint: str, method: str = 'GET', 
                        payload: Dict = None, iterations: int = 5) -> Dict:
        """Measure response time for an endpoint"""
        url = f"{self.base_url}{endpoint}"
        headers = {}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        
        times = []
        errors = 0
        
        for _ in range(iterations):
            try:
                start = time.time()
                
                if method == 'GET':
                    response = requests.get(url, headers=headers)
                else:
                    response = requests.post(url, json=payload, headers=headers)
                
                elapsed = (time.time() - start) * 1000  # Convert to ms
                times.append(elapsed)
                
                if response.status_code >= 400:
                    errors += 1
                    
            except Exception as e:
                errors += 1
                print(f"Error testing {endpoint}: {e}")
        
        if times:
            return {
                'endpoint': endpoint,
                'method': method,
                'iterations': iterations,
                'avg_response_time_ms': statistics.mean(times),
                'min_response_time_ms': min(times),
                'max_response_time_ms': max(times),
                'std_dev_ms': statistics.stdev(times) if len(times) > 1 else 0,
                'p95_response_time_ms': sorted(times)[int(len(times) * 0.95)] if times else 0,
                'error_rate': errors / iterations
            }
        else:
            return {
                'endpoint': endpoint,
                'method': method,
                'error': 'All requests failed',
                'error_rate': 1.0
            }
    
    def measure_concurrent_load(self, endpoint: str, concurrent_users: int = 10,
                               duration_seconds: int = 30) -> Dict:
        """Measure endpoint performance under concurrent load"""
        url = f"{self.base_url}{endpoint}"
        headers = {}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        
        responses = []
        errors = 0
        start_time = time.time()
        
        def make_request():
            try:
                req_start = time.time()
                response = requests.get(url, headers=headers)
                req_time = (time.time() - req_start) * 1000
                return req_time, response.status_code
            except:
                return None, None
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrent_users) as executor:
            futures = []
            
            while time.time() - start_time < duration_seconds:
                futures.append(executor.submit(make_request))
                time.sleep(0.1)  # Space out requests slightly
            
            for future in concurrent.futures.as_completed(futures):
                req_time, status_code = future.result()
                if req_time:
                    responses.append(req_time)
                    if status_code >= 400:
                        errors += 1
                else:
                    errors += 1
        
        total_requests = len(futures)
        successful_requests = len(responses)
        
        return {
            'endpoint': endpoint,
            'concurrent_users': concurrent_users,
            'duration_seconds': duration_seconds,
            'total_requests': total_requests,
            'successful_requests': successful_requests,
            'requests_per_second': total_requests / duration_seconds,
            'avg_response_time_ms': statistics.mean(responses) if responses else 0,
            'p95_response_time_ms': sorted(responses)[int(len(responses) * 0.95)] if responses else 0,
            'p99_response_time_ms': sorted(responses)[int(len(responses) * 0.99)] if responses else 0,
            'error_rate': errors / total_requests if total_requests > 0 else 0
        }
    
    def test_frontend_performance(self):
        """Test frontend static asset loading"""
        print("Testing frontend performance...")
        
        # Test static assets
        static_endpoints = [
            '/',
            '/_next/static/chunks/framework.js',
            '/_next/static/css/app.css',
            '/favicon.ico'
        ]
        
        for endpoint in static_endpoints:
            result = self.measure_endpoint(endpoint)
            self.results['tests'].append({
                'category': 'frontend_static',
                **result
            })
    
    def test_api_performance(self):
        """Test API endpoint performance"""
        print("Testing API performance...")
        
        # Test read endpoints
        read_endpoints = [
            '/api/models',
            '/api/settings',
            '/api/assistants'
        ]
        
        for endpoint in read_endpoints:
            result = self.measure_endpoint(endpoint)
            self.results['tests'].append({
                'category': 'api_read',
                **result
            })
    
    def test_llm_performance(self):
        """Test LLM endpoint performance"""
        print("Testing LLM performance...")
        
        test_payload = {
            'provider': 'bedrock',
            'model': 'claude-haiku-4.5',
            'messages': [
                {'role': 'user', 'content': 'Hello, how are you?'}
            ]
        }
        
        # Test with different models
        models = ['claude-haiku-4.5', 'titan-lite']
        
        for model in models:
            test_payload['model'] = model
            result = self.measure_endpoint(
                '/api/proxy/llm',
                method='POST',
                payload=test_payload,
                iterations=3  # Fewer iterations for expensive LLM calls
            )
            self.results['tests'].append({
                'category': 'llm_inference',
                'model': model,
                **result
            })
    
    def test_concurrent_load(self):
        """Test system under concurrent load"""
        print("Testing concurrent load handling...")
        
        # Test homepage under load
        result = self.measure_concurrent_load('/', concurrent_users=20, duration_seconds=30)
        self.results['tests'].append({
            'category': 'load_test',
            **result
        })
        
        # Test API under load
        result = self.measure_concurrent_load('/api/models', concurrent_users=10, duration_seconds=20)
        self.results['tests'].append({
            'category': 'load_test',
            **result
        })
    
    def publish_to_cloudwatch(self):
        """Publish metrics to CloudWatch"""
        try:
            metrics = []
            
            for test in self.results['tests']:
                if 'avg_response_time_ms' in test:
                    metrics.append({
                        'MetricName': f"{test['category']}_response_time",
                        'Value': test['avg_response_time_ms'],
                        'Unit': 'Milliseconds',
                        'Dimensions': [
                            {
                                'Name': 'Endpoint',
                                'Value': test.get('endpoint', 'unknown')
                            }
                        ]
                    })
                
                if 'error_rate' in test:
                    metrics.append({
                        'MetricName': f"{test['category']}_error_rate",
                        'Value': test['error_rate'] * 100,
                        'Unit': 'Percent',
                        'Dimensions': [
                            {
                                'Name': 'Endpoint',
                                'Value': test.get('endpoint', 'unknown')
                            }
                        ]
                    })
            
            # Publish in batches of 20
            for i in range(0, len(metrics), 20):
                batch = metrics[i:i+20]
                self.cloudwatch.put_metric_data(
                    Namespace='Amplify/Performance',
                    MetricData=batch
                )
            
            print(f"Published {len(metrics)} metrics to CloudWatch")
            
        except Exception as e:
            print(f"Failed to publish to CloudWatch: {e}")
    
    def generate_report(self):
        """Generate performance report"""
        print("\n" + "="*80)
        print("AMPLIFY PLATFORM PERFORMANCE REPORT")
        print("="*80)
        print(f"Timestamp: {self.results['timestamp']}")
        print(f"Base URL: {self.base_url}")
        print("\n")
        
        # Group results by category
        categories = {}
        for test in self.results['tests']:
            category = test.get('category', 'unknown')
            if category not in categories:
                categories[category] = []
            categories[category].append(test)
        
        # Print results by category
        for category, tests in categories.items():
            print(f"\n{category.upper().replace('_', ' ')}:")
            print("-" * 60)
            
            for test in tests:
                if 'error' in test:
                    print(f"  {test['endpoint']}: ERROR - {test['error']}")
                else:
                    print(f"  {test['endpoint']}:")
                    print(f"    Average: {test.get('avg_response_time_ms', 0):.2f}ms")
                    print(f"    Min/Max: {test.get('min_response_time_ms', 0):.2f}ms / {test.get('max_response_time_ms', 0):.2f}ms")
                    if 'p95_response_time_ms' in test:
                        print(f"    P95: {test['p95_response_time_ms']:.2f}ms")
                    if 'requests_per_second' in test:
                        print(f"    RPS: {test['requests_per_second']:.2f}")
                    if test.get('error_rate', 0) > 0:
                        print(f"    Error Rate: {test['error_rate']*100:.1f}%")
        
        # Summary
        print("\n" + "="*80)
        print("SUMMARY:")
        all_response_times = [t['avg_response_time_ms'] for t in self.results['tests'] 
                             if 'avg_response_time_ms' in t and t.get('category') != 'llm_inference']
        if all_response_times:
            print(f"Overall Average Response Time: {statistics.mean(all_response_times):.2f}ms")
        
        error_rates = [t['error_rate'] for t in self.results['tests'] if 'error_rate' in t]
        if error_rates:
            print(f"Overall Error Rate: {statistics.mean(error_rates)*100:.1f}%")
        
        # Save to file
        with open(f"performance_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json", 'w') as f:
            json.dump(self.results, f, indent=2)
        
        return self.results
    
    def run_all_tests(self):
        """Run all performance tests"""
        self.test_frontend_performance()
        self.test_api_performance()
        self.test_llm_performance()
        self.test_concurrent_load()
        self.generate_report()
        self.publish_to_cloudwatch()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python performance-monitor.py <base_url> [api_key]")
        sys.exit(1)
    
    base_url = sys.argv[1]
    api_key = sys.argv[2] if len(sys.argv) > 2 else None
    
    monitor = PerformanceMonitor(base_url, api_key)
    monitor.run_all_tests()