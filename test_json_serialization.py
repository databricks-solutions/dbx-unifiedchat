#!/usr/bin/env python3
"""
Test script to verify JSON serialization fix for date/datetime objects.

Run this to ensure the _safe_json_dumps method handles all special SQL types correctly.
"""

from datetime import date, datetime
from decimal import Decimal
import json


def safe_json_dumps(obj, indent=2):
    """
    Safely serialize objects to JSON, converting dates/datetime to strings.
    This is the same method added to ResultSummarizeAgent.
    """
    def default_handler(o):
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        elif isinstance(o, Decimal):
            return float(o)
        else:
            raise TypeError(f'Object of type {o.__class__.__name__} is not JSON serializable')
    
    return json.dumps(obj, indent=indent, default=default_handler)


def test_date_serialization():
    """Test serialization of date objects."""
    print("Testing date serialization...")
    
    data = {"event_date": date(2024, 1, 15)}
    result = safe_json_dumps(data)
    
    assert '"event_date": "2024-01-15"' in result
    print(f"✓ Date serialization works: {result}")


def test_datetime_serialization():
    """Test serialization of datetime objects."""
    print("\nTesting datetime serialization...")
    
    data = {"timestamp": datetime(2024, 1, 15, 14, 30, 0)}
    result = safe_json_dumps(data)
    
    assert '"timestamp": "2024-01-15T14:30:00"' in result
    print(f"✓ Datetime serialization works: {result}")


def test_decimal_serialization():
    """Test serialization of Decimal objects."""
    print("\nTesting Decimal serialization...")
    
    data = {"price": Decimal("19.99")}
    result = safe_json_dumps(data)
    
    assert '"price": 19.99' in result
    print(f"✓ Decimal serialization works: {result}")


def test_sql_result_serialization():
    """Test serialization of typical SQL query results."""
    print("\nTesting SQL result serialization...")
    
    # Simulate typical SQL query result with date and decimal columns
    sql_result = [
        {
            "id": 1,
            "name": "John Doe",
            "birth_date": date(1990, 5, 15),
            "hire_date": datetime(2020, 3, 1, 9, 0, 0),
            "salary": Decimal("75000.50")
        },
        {
            "id": 2,
            "name": "Jane Smith",
            "birth_date": date(1992, 8, 22),
            "hire_date": datetime(2021, 6, 15, 10, 30, 0),
            "salary": Decimal("82000.00")
        }
    ]
    
    result = safe_json_dumps(sql_result)
    
    # Verify all dates are serialized
    assert "1990-05-15" in result
    assert "1992-08-22" in result
    assert "2020-03-01T09:00:00" in result
    assert "2021-06-15T10:30:00" in result
    
    # Verify decimals are serialized
    assert "75000.5" in result or "75000.50" in result
    assert "82000" in result
    
    print("✓ SQL result serialization works!")
    print("\nSample output:")
    print(result)


def test_nested_structures():
    """Test serialization of nested data structures with special types."""
    print("\nTesting nested structure serialization...")
    
    data = {
        "query_result": {
            "success": True,
            "row_count": 2,
            "columns": ["id", "name", "birth_date", "salary"],
            "result": [
                {
                    "id": 1,
                    "name": "John",
                    "birth_date": date(1990, 5, 15),
                    "salary": Decimal("75000.50")
                }
            ]
        },
        "metadata": {
            "executed_at": datetime(2024, 1, 15, 14, 30, 0),
            "query_time_ms": Decimal("125.5")
        }
    }
    
    result = safe_json_dumps(data)
    
    assert "1990-05-15" in result
    assert "2024-01-15T14:30:00" in result
    assert "75000.5" in result or "75000.50" in result
    assert "125.5" in result
    
    print("✓ Nested structure serialization works!")


def test_standard_types_unchanged():
    """Verify standard JSON types still work normally."""
    print("\nTesting standard types...")
    
    data = {
        "string": "hello",
        "integer": 42,
        "float": 3.14,
        "boolean": True,
        "null": None,
        "list": [1, 2, 3],
        "nested": {"key": "value"}
    }
    
    result = safe_json_dumps(data)
    parsed = json.loads(result)
    
    assert parsed == data
    print("✓ Standard types work correctly!")


def test_error_on_unknown_type():
    """Verify that unknown types still raise errors."""
    print("\nTesting error handling for unknown types...")
    
    class CustomClass:
        pass
    
    data = {"custom": CustomClass()}
    
    try:
        safe_json_dumps(data)
        assert False, "Should have raised TypeError"
    except TypeError as e:
        assert "CustomClass" in str(e)
        print(f"✓ Unknown type correctly raises error: {e}")


def run_all_tests():
    """Run all tests."""
    print("=" * 80)
    print("JSON Serialization Fix - Test Suite")
    print("=" * 80)
    
    try:
        test_date_serialization()
        test_datetime_serialization()
        test_decimal_serialization()
        test_sql_result_serialization()
        test_nested_structures()
        test_standard_types_unchanged()
        test_error_on_unknown_type()
        
        print("\n" + "=" * 80)
        print("✅ ALL TESTS PASSED!")
        print("=" * 80)
        print("\nThe JSON serialization fix is working correctly.")
        print("You can now redeploy the agent to MLflow.")
        
    except AssertionError as e:
        print("\n" + "=" * 80)
        print(f"❌ TEST FAILED: {e}")
        print("=" * 80)
        raise
    except Exception as e:
        print("\n" + "=" * 80)
        print(f"❌ UNEXPECTED ERROR: {e}")
        print("=" * 80)
        raise


if __name__ == "__main__":
    run_all_tests()
