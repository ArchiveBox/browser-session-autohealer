// LLVM's SME prologues can execute non-streaming CNTD before SMSTART.
// Apple Silicon Linux VMs expose SME without SVE; that instruction SIGILLs.
// Keep Chrome's existing binary, using its other SIMD implementations only on
// this affected CPU combination. Do not change any other capability bits.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <sys/auxv.h>
#if defined(__aarch64__)
#include <asm/hwcap.h>
#endif

unsigned long getauxval(unsigned long type) {
    unsigned long (*original)(unsigned long) = dlsym(RTLD_NEXT, "getauxval");
    unsigned long value = original(type);
#if defined(__aarch64__)
    if (type == AT_HWCAP2 && !(original(AT_HWCAP) & HWCAP_SVE)) {
        value &= ~(HWCAP2_SME | HWCAP2_SME2);
    }
#endif
    return value;
}
