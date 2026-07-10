#!/usr/bin/env python3
"""
Test script for Redis cache implementation
"""

import os
import json
import time
from llm_router.service.cache import LLMResponseCache

def test_cache_operations():
    """Test basic cache operations"""
    print("Testing Redis cache implementation...")
    
    # Initialize cache
    os.environ['REDIS_CACHE_ENABLED'] = 'true'
    os.environ['REDIS_HOST'] = 'localhost'  # Update for production
    os.environ['REDIS_PORT'] = '6379'
    
    cache = LLMResponseCache()
    
    # Test data
    provider = 'bedrock'
    model = 'claude-haiku-4.5'
    messages = [
        {'role': 'user', 'content': 'What is the capital of France?'}
    ]
    response = {
        'content': 'The capital of France is Paris.',
        'model': model,
        'usage': {'total_tokens': 50}
    }
    
    # Test 1: Cache miss
    print("\nTest 1: Cache miss")
    start = time.time()
    cached = cache.get(provider, model, messages, temperature=0.1)
    elapsed = (time.time() - start) * 1000
    print(f"  Result: {cached}")
    print(f"  Time: {elapsed:.2f}ms")
    assert cached is None, "Expected cache miss"
    
    # Test 2: Cache set
    print("\nTest 2: Cache set")
    cache.set(provider, model, messages, response, temperature=0.1, ttl_seconds=60)
    print("  Response cached successfully")
    
    # Test 3: Cache hit
    print("\nTest 3: Cache hit")
    start = time.time()
    cached = cache.get(provider, model, messages, temperature=0.1)
    elapsed = (time.time() - start) * 1000
    print(f"  Result: {json.dumps(cached, indent=2)}")
    print(f"  Time: {elapsed:.2f}ms")
    assert cached is not None, "Expected cache hit"
    assert cached['response']['content'] == response['content'], "Cached content mismatch"
    
    # Test 4: High temperature (no cache)
    print("\nTest 4: High temperature (should not cache)")
    high_temp_response = cache.get(provider, model, messages, temperature=0.9)
    assert high_temp_response is None, "High temperature responses should not be cached"
    print("  Correctly skipped caching for high temperature")
    
    # Test 5: Cache invalidation
    print("\nTest 5: Cache invalidation")
    cache.invalidate_pattern(f"{provider}:{model}")
    cached = cache.get(provider, model, messages, temperature=0.1)
    assert cached is None, "Expected cache miss after invalidation"
    print("  Cache invalidated successfully")
    
    # Test 6: Cache stats
    print("\nTest 6: Cache statistics")
    stats = cache.get_stats()
    print(f"  Stats: {json.dumps(stats, indent=2)}")
    
    print("\n✅ All cache tests passed!")
    return True

if __name__ == "__main__":
    try:
        test_cache_operations()
    except Exception as e:
        print(f"\n❌ Cache test failed: {e}")
        print("\nMake sure Redis is running locally or update the connection settings.")
        print("To run Redis locally: docker run -p 6379:6379 redis:alpine")