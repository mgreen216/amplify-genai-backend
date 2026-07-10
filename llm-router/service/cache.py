import os
import json
import hashlib
import redis
import time
from typing import Optional, Dict, Any
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)


class LLMResponseCache:
    """
    Redis-based caching for LLM responses to reduce latency and costs.
    Implements intelligent caching based on model, prompt, and parameters.
    """
    
    def __init__(self):
        self.redis_client = None
        self.enabled = os.environ.get('REDIS_CACHE_ENABLED', 'false').lower() == 'true'
        
        if self.enabled:
            try:
                redis_host = os.environ.get('REDIS_HOST', 'localhost')
                redis_port = int(os.environ.get('REDIS_PORT', '6379'))
                redis_db = int(os.environ.get('REDIS_DB', '0'))
                redis_password = os.environ.get('REDIS_PASSWORD')
                
                self.redis_client = redis.Redis(
                    host=redis_host,
                    port=redis_port,
                    db=redis_db,
                    password=redis_password,
                    decode_responses=True,
                    socket_timeout=5,
                    socket_connect_timeout=5,
                    retry_on_timeout=True,
                    health_check_interval=30
                )
                
                # Test connection
                self.redis_client.ping()
                logger.info(f"Redis cache connected to {redis_host}:{redis_port}")
                
            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}")
                self.enabled = False
    
    def _generate_cache_key(self, provider: str, model: str, messages: list, 
                          temperature: float = None, max_tokens: int = None) -> str:
        """Generate a deterministic cache key based on request parameters."""
        # Create a dictionary of cache-relevant parameters
        cache_params = {
            'provider': provider,
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens
        }
        
        # Sort keys for consistency
        cache_str = json.dumps(cache_params, sort_keys=True)
        
        # Generate hash
        cache_hash = hashlib.sha256(cache_str.encode()).hexdigest()
        
        return f"llm:response:{provider}:{model}:{cache_hash[:16]}"
    
    def get(self, provider: str, model: str, messages: list, 
            temperature: float = None, max_tokens: int = None) -> Optional[Dict[str, Any]]:
        """Retrieve cached response if available."""
        if not self.enabled or not self.redis_client:
            return None
        
        # Don't cache responses with high temperature (creative outputs)
        if temperature and temperature > 0.7:
            return None
        
        try:
            cache_key = self._generate_cache_key(provider, model, messages, temperature, max_tokens)
            cached_data = self.redis_client.get(cache_key)
            
            if cached_data:
                logger.info(f"Cache HIT for key: {cache_key}")
                return json.loads(cached_data)
            else:
                logger.debug(f"Cache MISS for key: {cache_key}")
                return None
                
        except Exception as e:
            logger.error(f"Cache get error: {e}")
            return None
    
    def set(self, provider: str, model: str, messages: list, response: Dict[str, Any],
            temperature: float = None, max_tokens: int = None, ttl_seconds: int = None):
        """Cache the LLM response with appropriate TTL."""
        if not self.enabled or not self.redis_client:
            return
        
        # Don't cache responses with high temperature
        if temperature and temperature > 0.7:
            return
        
        try:
            cache_key = self._generate_cache_key(provider, model, messages, temperature, max_tokens)
            
            # Determine TTL based on content type
            if not ttl_seconds:
                # Longer cache for factual/deterministic responses
                if temperature is None or temperature < 0.3:
                    ttl_seconds = 3600  # 1 hour
                # Medium cache for semi-deterministic responses
                elif temperature < 0.7:
                    ttl_seconds = 1800  # 30 minutes
                else:
                    return  # Don't cache creative responses
            
            # Add metadata to cached response
            cache_data = {
                'response': response,
                'cached_at': int(time.time()),
                'provider': provider,
                'model': model
            }
            
            self.redis_client.setex(
                cache_key,
                ttl_seconds,
                json.dumps(cache_data)
            )
            
            logger.info(f"Cached response for key: {cache_key}, TTL: {ttl_seconds}s")
            
        except Exception as e:
            logger.error(f"Cache set error: {e}")
    
    def invalidate_pattern(self, pattern: str):
        """Invalidate all cache entries matching a pattern."""
        if not self.enabled or not self.redis_client:
            return
        
        try:
            # Use SCAN to avoid blocking on large keyspaces
            cursor = 0
            deleted_count = 0
            
            while True:
                cursor, keys = self.redis_client.scan(
                    cursor=cursor,
                    match=f"llm:response:{pattern}*",
                    count=100
                )
                
                if keys:
                    deleted_count += self.redis_client.delete(*keys)
                
                if cursor == 0:
                    break
            
            logger.info(f"Invalidated {deleted_count} cache entries matching pattern: {pattern}")
            
        except Exception as e:
            logger.error(f"Cache invalidation error: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        if not self.enabled or not self.redis_client:
            return {'enabled': False}
        
        try:
            info = self.redis_client.info('stats')
            memory = self.redis_client.info('memory')
            
            return {
                'enabled': True,
                'hits': info.get('keyspace_hits', 0),
                'misses': info.get('keyspace_misses', 0),
                'hit_rate': info.get('keyspace_hits', 0) / max(info.get('keyspace_hits', 0) + info.get('keyspace_misses', 0), 1),
                'memory_used': memory.get('used_memory_human', 'N/A'),
                'connected_clients': info.get('connected_clients', 0)
            }
            
        except Exception as e:
            logger.error(f"Failed to get cache stats: {e}")
            return {'enabled': True, 'error': str(e)}


# Global cache instance
llm_cache = LLMResponseCache()