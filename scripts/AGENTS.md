# Crawler and test maintenance

- Adding a page ID, department, section, or registry row that uses an existing parser does not require a new test file. Validate the configuration with the existing registry or route checks and inspect a small sample of crawl output.
- Add or extend a test only for a distinct parser behavior, a reproduced regression, a data-loss risk, or a safety boundary. Put new cases in the existing test module for that parser or collector family.
- Do not pin total page, department, or section counts, positional section indexes, or the complete current registry in tests. Crawl scope is expected to grow.
- Keep production validation in the crawler's config, preflight, or parsing code. Test doubles and fixture builders belong in tests and must not be imported by crawler runtime code.
- Run the relevant test module while editing; run the full suite when shared crawler behavior changes. Tests are never part of normal crawl execution.
