import { configure } from "@testing-library/react";

// Async lookups (findBy*/waitFor) wait on mocked fetches and effects; the 1 s default times out when
// the whole suite runs in parallel on a loaded machine. A real failure still fails, only later.
configure({ asyncUtilTimeout: 5000 });
