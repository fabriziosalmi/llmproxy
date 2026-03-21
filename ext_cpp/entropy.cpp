#include <pybind11/pybind11.h>
#include <pybind11/numpy.h>
#include <cmath>
#include <unordered_map>

namespace py = pybind11;

double compute_entropy(py::array_t<int> tokens) {
    auto buf = tokens.request();
    int* ptr = static_cast<int*>(buf.ptr);
    size_t size = buf.size;
    
    if (size == 0) return 0.0;
    
    double entropy = 0.0;
    {
        py::gil_scoped_release release; // Release the Python GIL for raw iteration
        
        std::unordered_map<int, double> counts;
    for (size_t i = 0; i < size; ++i) {
        counts[ptr[i]]++;
    }
    
    double entropy = 0.0;
        for (const auto& pair : counts) {
            double p = pair.second / size;
            entropy -= p * std::log2(p);
        }
    }
    
    return entropy;
}

PYBIND11_MODULE(entropy_c, m) {
    m.doc() = "High-performance O(1) Shannon Entropy calculator with GIL dropping.";
    m.def("compute_entropy", &compute_entropy, "Computes Shannon entropy from an array of token IDs");
}
