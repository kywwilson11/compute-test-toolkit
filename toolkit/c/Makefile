CC      ?= cc
CFLAGS  ?= -O2 -Wall -Wextra -std=c11

pcie_bert: pcie_bert.c
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f pcie_bert

.PHONY: clean
