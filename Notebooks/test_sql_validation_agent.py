# Databricks notebook source
# DBTITLE 1,Test SQL Validation Agent
"""
Test Suite for SQL Validation Agent

This notebook demonstrates the SQL Validation Agent with various test cases:
1. Valid SQL queries
2. Invalid table names
3. Invalid column names
4. Complex multi-table JOINs
5. Edge cases and error scenarios
"""

# COMMAND ----------

# DBTITLE 1,Setup - Import Required Modules
# Import the SQL Validation Agent
%run ./sql_validation_agent

import json
from typing import Dict, Any

# COMMAND ----------

# DBTITLE 1,Helper Function - Pretty Print Results
def print_validation_result(result: Dict[str, Any], test_name: str):
    """
    Pretty print validation results for easy reading.
    """
    print("\n" + "="*80)
    print(f"TEST: {test_name}")
    print("="*80)
    
    if result["is_valid"]:
        print("✅ STATUS: VALID")
    else:
        print("❌ STATUS: INVALID")
    
    print(f"\n📊 Summary:")
    print(f"  - Tables Extracted: {len(result['validation_details']['tables']['extracted'])}")
    print(f"  - Errors: {len(result.get('errors', []))}")
    print(f"  - Warnings: {len(result.get('warnings', []))}")
    print(f"  - Suggestions: {len(result.get('suggestions', []))}")
    
    if result.get('errors'):
        print(f"\n❌ Errors:")
        for error in result['errors']:
            print(f"  - {error}")
    
    if result.get('warnings'):
        print(f"\n⚠️  Warnings:")
        for warning in result['warnings']:
            print(f"  - {warning}")
    
    if result.get('suggestions'):
        print(f"\n💡 Suggestions:")
        for suggestion in result['suggestions']:
            print(f"  - {suggestion}")
    
    print("\n" + "="*80)

# COMMAND ----------

# DBTITLE 1,Test Case 1: Valid SQL Query - Simple SELECT
"""
Test Case 1: Valid SQL with single table
Expected: Should pass validation
"""

test_sql_1 = """
SELECT 
    patient_id,
    allowed_amount,
    service_date
FROM yyang.multi_agent_genie.medical_claim
WHERE service_date >= '2024-01-01'
LIMIT 100
"""

result_1 = validate_sql_with_agent(
    sql_query=test_sql_1,
    context={
        "test_case": "simple_select",
        "relevant_space_ids": ["01f0956a387714969edde65458dcc22a"]
    }
)

print_validation_result(result_1, "Test Case 1: Valid Simple SELECT")

# COMMAND ----------

# DBTITLE 1,Test Case 2: Valid SQL Query - Multi-Table JOIN
"""
Test Case 2: Valid SQL with multiple table JOINs
Expected: Should pass validation
"""

test_sql_2 = """
SELECT 
    mc.patient_id,
    mc.allowed_amount,
    mc.payer_type,
    d.icd10_code,
    e.birth_year,
    CASE 
        WHEN YEAR(CURRENT_DATE()) - e.birth_year < 18 THEN '0-17'
        WHEN YEAR(CURRENT_DATE()) - e.birth_year BETWEEN 18 AND 34 THEN '18-34'
        WHEN YEAR(CURRENT_DATE()) - e.birth_year BETWEEN 35 AND 49 THEN '35-49'
        WHEN YEAR(CURRENT_DATE()) - e.birth_year BETWEEN 50 AND 64 THEN '50-64'
        ELSE '65+'
    END AS age_group
FROM yyang.multi_agent_genie.medical_claim mc
JOIN yyang.multi_agent_genie.diagnosis d ON mc.claim_id = d.claim_id
JOIN yyang.multi_agent_genie.enrollment e ON mc.patient_id = e.patient_id
WHERE d.icd10_code LIKE 'E11%'
GROUP BY mc.payer_type, age_group
"""

result_2 = validate_sql_with_agent(
    sql_query=test_sql_2,
    context={
        "test_case": "multi_table_join",
        "relevant_space_ids": [
            "01f0956a387714969edde65458dcc22a",
            "01f0956a4b0512e2a8aa325ffbac821b",
            "01f0956a54af123e9cd23907e8167df9"
        ]
    }
)

print_validation_result(result_2, "Test Case 2: Valid Multi-Table JOIN")

# COMMAND ----------

# DBTITLE 1,Test Case 3: Invalid Table Name
"""
Test Case 3: SQL with non-existent table
Expected: Should fail validation with table error
"""

test_sql_3 = """
SELECT 
    patient_id,
    total_cost
FROM yyang.multi_agent_genie.patient_records
WHERE patient_id > 1000
"""

result_3 = validate_sql_with_agent(
    sql_query=test_sql_3,
    context={
        "test_case": "invalid_table"
    }
)

print_validation_result(result_3, "Test Case 3: Invalid Table Name")

# COMMAND ----------

# DBTITLE 1,Test Case 4: Invalid Column Name
"""
Test Case 4: SQL with non-existent column
Expected: Should fail validation with column error
"""

test_sql_4 = """
SELECT 
    patient_id,
    total_cost_amount,
    claim_submission_date
FROM yyang.multi_agent_genie.medical_claim
WHERE total_cost_amount > 5000
"""

result_4 = validate_sql_with_agent(
    sql_query=test_sql_4,
    context={
        "test_case": "invalid_column",
        "relevant_space_ids": ["01f0956a387714969edde65458dcc22a"]
    }
)

print_validation_result(result_4, "Test Case 4: Invalid Column Name")

# COMMAND ----------

# DBTITLE 1,Test Case 5: Mixed Valid and Invalid
"""
Test Case 5: SQL with one valid table and one invalid table
Expected: Should fail validation, identify which table is invalid
"""

test_sql_5 = """
SELECT 
    mc.patient_id,
    mc.allowed_amount,
    pr.record_date
FROM yyang.multi_agent_genie.medical_claim mc
JOIN yyang.multi_agent_genie.patient_records pr 
    ON mc.patient_id = pr.patient_id
"""

result_5 = validate_sql_with_agent(
    sql_query=test_sql_5,
    context={
        "test_case": "mixed_valid_invalid"
    }
)

print_validation_result(result_5, "Test Case 5: Mixed Valid and Invalid Tables")

# COMMAND ----------

# DBTITLE 1,Test Case 6: Complex Aggregation
"""
Test Case 6: Valid SQL with complex aggregations
Expected: Should pass validation
"""

test_sql_6 = """
SELECT 
    mc.payer_type,
    COUNT(DISTINCT mc.patient_id) as unique_patients,
    COUNT(mc.claim_id) as total_claims,
    AVG(mc.allowed_amount) as avg_claim_cost,
    SUM(mc.allowed_amount) as total_cost,
    MIN(mc.service_date) as earliest_claim,
    MAX(mc.service_date) as latest_claim
FROM yyang.multi_agent_genie.medical_claim mc
WHERE mc.service_date BETWEEN '2024-01-01' AND '2024-12-31'
GROUP BY mc.payer_type
HAVING COUNT(mc.claim_id) > 100
ORDER BY total_cost DESC
"""

result_6 = validate_sql_with_agent(
    sql_query=test_sql_6,
    context={
        "test_case": "complex_aggregation",
        "relevant_space_ids": ["01f0956a387714969edde65458dcc22a"]
    }
)

print_validation_result(result_6, "Test Case 6: Complex Aggregation")

# COMMAND ----------

# DBTITLE 1,Test Case 7: Subquery
"""
Test Case 7: Valid SQL with subquery
Expected: Should pass validation (if tables exist)
"""

test_sql_7 = """
SELECT 
    patient_id,
    allowed_amount
FROM yyang.multi_agent_genie.medical_claim
WHERE patient_id IN (
    SELECT DISTINCT patient_id
    FROM yyang.multi_agent_genie.diagnosis
    WHERE icd10_code LIKE 'E11%'
)
ORDER BY allowed_amount DESC
LIMIT 50
"""

result_7 = validate_sql_with_agent(
    sql_query=test_sql_7,
    context={
        "test_case": "subquery",
        "relevant_space_ids": [
            "01f0956a387714969edde65458dcc22a",
            "01f0956a4b0512e2a8aa325ffbac821b"
        ]
    }
)

print_validation_result(result_7, "Test Case 7: Subquery")

# COMMAND ----------

# DBTITLE 1,Test Case 8: Invalid Syntax
"""
Test Case 8: SQL with syntax error (missing FROM)
Expected: Should fail validation with syntax error
"""

test_sql_8 = """
SELECT 
    patient_id,
    allowed_amount
WHERE service_date > '2024-01-01'
"""

result_8 = validate_sql_with_agent(
    sql_query=test_sql_8,
    context={
        "test_case": "invalid_syntax"
    }
)

print_validation_result(result_8, "Test Case 8: Invalid Syntax")

# COMMAND ----------

# DBTITLE 1,Test Case 9: Ambiguous Aliases
"""
Test Case 9: SQL with short/ambiguous aliases
Expected: Should pass but may have warnings
"""

test_sql_9 = """
SELECT 
    c.patient_id,
    c.allowed_amount,
    d.icd10_code
FROM yyang.multi_agent_genie.medical_claim c
JOIN yyang.multi_agent_genie.diagnosis d ON c.claim_id = d.claim_id
"""

result_9 = validate_sql_with_agent(
    sql_query=test_sql_9,
    context={
        "test_case": "ambiguous_aliases"
    }
)

print_validation_result(result_9, "Test Case 9: Ambiguous Aliases")

# COMMAND ----------

# DBTITLE 1,Test Case 10: Case Sensitivity
"""
Test Case 10: SQL with mixed case column names
Expected: Should handle case sensitivity appropriately
"""

test_sql_10 = """
SELECT 
    PATIENT_ID,
    Allowed_Amount,
    service_DATE
FROM yyang.multi_agent_genie.medical_claim
"""

result_10 = validate_sql_with_agent(
    sql_query=test_sql_10,
    context={
        "test_case": "case_sensitivity"
    }
)

print_validation_result(result_10, "Test Case 10: Case Sensitivity")

# COMMAND ----------

# DBTITLE 1,Test Summary Report
"""
Generate a summary report of all test cases
"""

all_results = [
    ("Test 1: Valid Simple SELECT", result_1),
    ("Test 2: Valid Multi-Table JOIN", result_2),
    ("Test 3: Invalid Table Name", result_3),
    ("Test 4: Invalid Column Name", result_4),
    ("Test 5: Mixed Valid and Invalid", result_5),
    ("Test 6: Complex Aggregation", result_6),
    ("Test 7: Subquery", result_7),
    ("Test 8: Invalid Syntax", result_8),
    ("Test 9: Ambiguous Aliases", result_9),
    ("Test 10: Case Sensitivity", result_10),
]

print("\n" + "="*80)
print("SQL VALIDATION AGENT - TEST SUMMARY REPORT")
print("="*80)

passed = sum(1 for _, r in all_results if r["is_valid"])
failed = len(all_results) - passed

print(f"\n📊 Overall Results:")
print(f"  - Total Tests: {len(all_results)}")
print(f"  - Passed: {passed}")
print(f"  - Failed: {failed}")
print(f"  - Success Rate: {passed/len(all_results)*100:.1f}%")

print(f"\n📋 Detailed Results:")
for name, result in all_results:
    status = "✅ VALID" if result["is_valid"] else "❌ INVALID"
    error_count = len(result.get("errors", []))
    warning_count = len(result.get("warnings", []))
    print(f"  {status} | {name} | Errors: {error_count} | Warnings: {warning_count}")

print("\n" + "="*80)
print("Test suite execution complete!")
print("="*80)

# COMMAND ----------

# DBTITLE 1,Integration Test with SQL Synthesis Agent
"""
Integration Test: Validate output from SQL Synthesis Agent

This demonstrates the full workflow:
1. Planning Agent creates execution plan
2. SQL Synthesis Agent generates SQL
3. SQL Validation Agent validates the SQL
4. SQL Execution Agent executes (if valid)
"""

# Simulate output from Planning Agent
mock_plan_result = {
    "original_query": "What is the average cost of medical claims for patients diagnosed with diabetes?",
    "question_clear": True,
    "sub_questions": [
        "Identify diabetes patients from diagnosis codes",
        "Get their medical claims",
        "Calculate average cost"
    ],
    "requires_multiple_spaces": True,
    "relevant_space_ids": [
        "01f0956a387714969edde65458dcc22a",
        "01f0956a4b0512e2a8aa325ffbac821b"
    ],
    "requires_join": True,
    "join_strategy": "fast_route",
    "execution_plan": "JOIN medical_claim with diagnosis on claim_id, filter diabetes ICD-10 codes, calculate AVG"
}

# Simulate output from SQL Synthesis Agent
mock_synthesized_sql = """
SELECT 
    AVG(mc.allowed_amount) as avg_diabetes_claim_cost,
    COUNT(DISTINCT mc.patient_id) as patient_count,
    COUNT(mc.claim_id) as claim_count
FROM yyang.multi_agent_genie.medical_claim mc
JOIN yyang.multi_agent_genie.diagnosis d ON mc.claim_id = d.claim_id
WHERE d.icd10_code LIKE 'E11%'
"""

print("\n" + "="*80)
print("INTEGRATION TEST: SQL Synthesis → Validation")
print("="*80)

print("\n📝 Step 1: Planning Agent Output")
print(json.dumps(mock_plan_result, indent=2))

print("\n💡 Step 2: SQL Synthesis Agent Output")
print(mock_synthesized_sql)

print("\n🔍 Step 3: SQL Validation Agent Processing...")

# Validate the synthesized SQL
integration_result = validate_sql_with_agent(
    sql_query=mock_synthesized_sql,
    context=mock_plan_result
)

print_validation_result(integration_result, "Integration Test")

if integration_result["is_valid"]:
    print("\n✅ READY FOR EXECUTION")
    print("   → Next step: SQL Execution Agent")
else:
    print("\n❌ VALIDATION FAILED - RETURN TO SQL SYNTHESIS")
    print("   → Send errors back to SQL Synthesis Agent for correction")
    print(f"\n   Errors to address:")
    for error in integration_result.get("errors", []):
        print(f"      - {error}")

print("\n" + "="*80)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Test Results Analysis
# MAGIC 
# MAGIC ### Expected Outcomes:
# MAGIC 
# MAGIC 1. ✅ **Test 1-2**: Should PASS - Valid SQL with correct tables/columns
# MAGIC 2. ❌ **Test 3**: Should FAIL - Invalid table name
# MAGIC 3. ❌ **Test 4**: Should FAIL - Invalid column names
# MAGIC 4. ❌ **Test 5**: Should FAIL - One invalid table
# MAGIC 5. ✅ **Test 6**: Should PASS - Complex but valid aggregation
# MAGIC 6. ✅ **Test 7**: Should PASS - Valid subquery
# MAGIC 7. ❌ **Test 8**: Should FAIL - Syntax error
# MAGIC 8. ⚠️ **Test 9**: May have WARNINGS - Ambiguous aliases
# MAGIC 9. ✅/❌ **Test 10**: Depends on case sensitivity rules
# MAGIC 
# MAGIC ### Key Validation Features Tested:
# MAGIC 
# MAGIC - ✅ Table existence checking
# MAGIC - ✅ Column existence checking  
# MAGIC - ✅ Alias resolution
# MAGIC - ✅ Syntax validation
# MAGIC - ✅ Multi-table JOIN validation
# MAGIC - ✅ Subquery handling
# MAGIC - ✅ Complex aggregation validation
# MAGIC - ✅ Error message generation
# MAGIC - ✅ Suggestion generation
# MAGIC 
# MAGIC ### Performance Metrics to Monitor:
# MAGIC 
# MAGIC 1. **Validation Time**: Should be < 5 seconds per query
# MAGIC 2. **Accuracy**: Should catch all invalid tables/columns
# MAGIC 3. **False Positives**: Should minimize incorrect failures
# MAGIC 4. **Suggestion Quality**: Suggestions should be actionable

# COMMAND ----------

print("="*80)
print("🎉 SQL VALIDATION AGENT TEST SUITE COMPLETE")
print("="*80)
print("\nAll test cases executed successfully!")
print("\nNext Steps:")
print("  1. Review test results above")
print("  2. Verify validation logic is working correctly")
print("  3. Integrate with Super Agent")
print("  4. Deploy to production")
print("="*80)
