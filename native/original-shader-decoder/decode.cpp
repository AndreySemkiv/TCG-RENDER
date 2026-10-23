// Offline data transformation only: native SMOL-V bytes -> SPIR-V -> readable GLSL.
// The SPIR-V is the decoded original program; GLSL is an analysis translation.
#include "smolv/smolv.h"
#include "spirv-cross/spirv_glsl.hpp"
#include <fstream>
#include <iostream>
#include <iterator>
#include <cstring>
#include <vector>

int main(int argc, char** argv) {
    if (argc != 3) { std::cerr << "input.smolv output-prefix\n"; return 2; }
    try {
        std::ifstream input(argv[1], std::ios::binary);
        if (!input) return 3;
        std::vector<unsigned char> bytes((std::istreambuf_iterator<char>(input)), {});
        auto size = smolv::GetDecodedBufferSize(bytes.data(), bytes.size());
        if (!size || size > 64 * 1024 * 1024 || size % 4) return 4;
        std::vector<uint32_t> words(size / 4);
        if (!smolv::Decode(bytes.data(), bytes.size(), words.data(), size)) return 5;
        if (words[0] != 0x07230203) return 6;
        std::string prefix(argv[2]);
        std::ofstream binary(prefix + ".spv", std::ios::binary);
        binary.write(reinterpret_cast<const char*>(words.data()), size); binary.close();
        spirv_cross::CompilerGLSL compiler(words);
        auto opts = compiler.get_common_options();
        opts.version = 450; opts.es = false; opts.vulkan_semantics = true;
        compiler.set_common_options(opts);
        auto code = compiler.compile();
        std::ofstream glsl(prefix + ".glsl"); glsl << code;
        std::cout << size << " decoded bytes; execution model " << compiler.get_execution_model() << "\n";
        return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << "\n"; return 7; }
}
