# Security Policy

## Reporting Security Vulnerabilities

Databricks takes the security of our software products and services seriously, including all source code repositories managed through our GitHub organizations.

If you believe you have found a security vulnerability in this repository, please report it to us through coordinated disclosure.

**Please do not report security vulnerabilities through public GitHub issues, discussions, or pull requests.**

Instead, please send an email to security[@]databricks.com.

Please include as much of the following information as possible to help us better understand and resolve the issue:

* The type of issue (e.g., buffer overflow, SQL injection, cross-site scripting, etc.)
* Full paths of source file(s) related to the manifestation of the issue
* The location of the affected source code (tag/branch/commit or direct URL)
* Any special configuration required to reproduce the issue
* Step-by-step instructions to reproduce the issue
* Proof-of-concept or exploit code (if possible)
* Impact of the issue, including how an attacker might exploit the issue

This information will help us triage your report more quickly.

## Responsible Disclosure Policy

We follow the principle of Coordinated Vulnerability Disclosure. Under this model:

* The vulnerability reporter allows Databricks a reasonable amount of time to fix the vulnerability before public disclosure
* Databricks commits to acknowledging and responding to vulnerability reports in a timely manner
* Databricks will work with the reporter to understand and resolve the issue
* Once the vulnerability is fixed, Databricks may publicly disclose the vulnerability (crediting the reporter if they wish)

## Preferred Languages

We prefer all communications to be in English.

## Support Disclaimer

Please note that this is a Field Solutions project provided for reference and educational purposes. While we take security seriously, this project is not covered by Databricks production support SLAs.

For security issues with Databricks production products and services, please refer to the official Databricks security reporting channels at https://www.databricks.com/trust.

## Security Best Practices

When using this project:

* **Credentials Management**: Never commit credentials, API keys, or tokens to the repository. Use environment variables or secure secret management systems.
* **Dependencies**: Regularly update dependencies to their latest stable versions to receive security patches.
* **Access Control**: Follow the principle of least privilege when configuring Databricks workspace permissions.
* **Data Protection**: Ensure sensitive data is properly encrypted at rest and in transit.
* **Audit Logging**: Enable and monitor audit logs in your Databricks workspace.

## Scope

This security policy applies to the latest version of the code in the main branch. Security issues in older versions or deprecated branches may not be addressed.

---

Thank you for helping to keep Databricks Field Solutions projects secure.
