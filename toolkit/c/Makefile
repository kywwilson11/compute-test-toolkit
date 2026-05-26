CC      ?= cc
CFLAGS  ?= -O2 -Wall -Wextra -std=c11

CORE = pcie_bert_core.c

pcie_bert: pcie_bert.c $(CORE) pcie_bert_core.h
	$(CC) $(CFLAGS) -o $@ pcie_bert.c $(CORE)

# Unit tests (Unity) — run the core + the in-memory config-space fake, off-hardware.
TEST_CFLAGS = -g -O0 -Wall -Wextra -std=c11 -I. -Itest
TEST_SRC    = test/test_pcie_bert.c $(CORE) test/fake_cfgspace.c test/unity.c

test/runner: $(TEST_SRC) pcie_bert_core.h test/fake_cfgspace.h
	$(CC) $(TEST_CFLAGS) -o $@ $(TEST_SRC)

ctest: test/runner
	./test/runner

clean:
	rm -f pcie_bert test/runner

.PHONY: clean ctest
