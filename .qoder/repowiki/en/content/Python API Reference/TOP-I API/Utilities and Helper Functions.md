# Utilities and Helper Functions

<cite>
**Referenced Files in This Document**
- [__init__.py](file://python/tvm/topi/__init__.py)
- [utils.py](file://python/tvm/topi/utils.py)
- [utils.h](file://include/tvm/topi/utils.h)
- [utils.cc](file://src/topi/utils.cc)
- [tags.h](file://include/tvm/topi/tags.h)
- [transform.h](file://include/tvm/topi/transform.h)
- [transform.cc](file://src/topi/transform.cc)
- [broadcast.h](file://include/tvm/topi/broadcast.h)
- [broadcast.cc](file://src/topi/broadcast.cc)
- [tensor_utils.h](file://include/tvm/topi/detail/tensor_utils.h)
- [ravel_unravel.h](file://include/tvm/topi/detail/ravel_unravel.h)
- [strided_slice.h](file://include/tvm/topi/detail/strided_slice.h)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)

## Introduction
This document explains the TOP-I (TOPI) utility functions and helper components that enable shape manipulation, tensor transformations, operator tagging, and generic operation implementations. It focuses on:
- Shape and layout conversion helpers
- Dimension handling and common tensor manipulation patterns
- Operator tagging mechanisms and their roles in scheduling
- Generic operation implementations for transforms and broadcasts
- Practical guidance for implementing custom operators and integrating with the broader TVM ecosystem

## Project Structure
TOP-I utilities are organized into Python APIs and C++/C headers with registration bridges:
- Python layer exposes convenience functions, shape inference, and scheduling helpers
- C++/C headers define low-level tensor operations and detail utilities
- Registration bridges bind Python-friendly APIs to packed functions for downstream consumers

```mermaid
graph TB
subgraph "Python Layer"
PY_API["python/tvm/topi/utils.py"]
PY_INIT["python/tvm/topi/__init__.py"]
end
subgraph "C++/C Headers"
H_TAGS["include/tvm/topi/tags.h"]
H_UTILS["include/tvm/topi/utils.h"]
H_TENSOR_UTILS["include/tvm/topi/detail/tensor_utils.h"]
H_RAVEL["include/tvm/topi/detail/ravel_unravel.h"]
H_STRSLICE["include/tvm/topi/detail/strided_slice.h"]
H_TRANSFORM["include/tvm/topi/transform.h"]
H_BROADCAST["include/tvm/topi/broadcast.h"]
end
subgraph "Registration Bridges"
CC_UTILS["src/topi/utils.cc"]
CC_TRANSFORM["src/topi/transform.cc"]
CC_BROADCAST["src/topi/broadcast.cc"]
end
PY_INIT --> PY_API
PY_API --> H_TAGS
PY_API --> H_TENSOR_UTILS
PY_API --> H_RAVEL
PY_API --> H_STRSLICE
PY_API --> H_TRANSFORM
PY_API --> H_BROADCAST
H_TAGS --> CC_UTILS
H_TAGS --> CC_TRANSFORM
H_TAGS --> CC_BROADCAST
H_UTILS --> CC_UTILS
H_TENSOR_UTILS --> CC_UTILS
H_RAVEL --> CC_TRANSFORM
H_STRSLICE --> CC_TRANSFORM
H_TRANSFORM --> CC_TRANSFORM
H_BROADCAST --> CC_BROADCAST
```

**Diagram sources**
- [__init__.py:35-56](file://python/tvm/topi/__init__.py#L35-L56)
- [utils.py:1-546](file://python/tvm/topi/utils.py#L1-L546)
- [tags.h:24-59](file://include/tvm/topi/tags.h#L24-L59)
- [utils.h:24-51](file://include/tvm/topi/utils.h#L24-L51)
- [tensor_utils.h:24-148](file://include/tvm/topi/detail/tensor_utils.h#L24-L148)
- [ravel_unravel.h:24-84](file://include/tvm/topi/detail/ravel_unravel.h#L24-L84)
- [strided_slice.h:24-155](file://include/tvm/topi/detail/strided_slice.h#L24-L155)
- [transform.h:24-52](file://include/tvm/topi/transform.h#L24-L52)
- [broadcast.h:24-497](file://include/tvm/topi/broadcast.h#L24-L497)
- [utils.cc:24-53](file://src/topi/utils.cc#L24-L53)
- [transform.cc:24-280](file://src/topi/transform.cc#L24-L280)
- [broadcast.cc:24-87](file://src/topi/broadcast.cc#L24-L87)

**Section sources**
- [__init__.py:19-65](file://python/tvm/topi/__init__.py#L19-L65)
- [utils.py:1-546](file://python/tvm/topi/utils.py#L1-L546)

## Core Components
- Operator tagging constants and predicates define semantics for scheduling and pass infra:
  - Tags include elementwise, injective, broadcast, matmul, convolutions, and einsum
  - Predicates classify tags as broadcast or injective for automated scheduling decisions
- Shape and layout utilities:
  - Empty-shape detection, ravel/unravel index helpers, strided-slice canonicalization
  - Layout shape inference via bijective layout mapping
- Transform operations:
  - Expand dims, transpose, reshape, squeeze, concatenate, stack, split, sliding window
  - Dynamic strided slice with axis-specific begin/end/stride handling
  - Index canonicalization helpers for static/dynamic cases
- Broadcast operations:
  - Auto-broadcasting elementwise ops with tensor/scalar variants
  - Broadcast-to with shape compatibility checks
- Packed-function registration:
  - Bridges Python APIs to C++ implementations for transforms, broadcasts, and sampling utilities

**Section sources**
- [tags.h:24-59](file://include/tvm/topi/tags.h#L24-L59)
- [utils.h:24-51](file://include/tvm/topi/utils.h#L24-L51)
- [tensor_utils.h:24-148](file://include/tvm/topi/detail/tensor_utils.h#L24-L148)
- [ravel_unravel.h:24-84](file://include/tvm/topi/detail/ravel_unravel.h#L24-L84)
- [strided_slice.h:24-155](file://include/tvm/topi/detail/strided_slice.h#L24-L155)
- [transform.h:53-800](file://include/tvm/topi/transform.h#L53-L800)
- [broadcast.h:24-497](file://include/tvm/topi/broadcast.h#L24-L497)
- [utils.cc:24-53](file://src/topi/utils.cc#L24-L53)
- [transform.cc:24-280](file://src/topi/transform.cc#L24-L280)
- [broadcast.cc:24-87](file://src/topi/broadcast.cc#L24-L87)

## Architecture Overview
The TOP-I utilities follow a layered architecture:
- Python layer provides ergonomic APIs and integrates with TVM’s scheduling and IR
- C++/C headers implement core operations and expose detail utilities
- Registration bridges register packed functions for dynamic dispatch
- Tagging system informs scheduling passes and fusion heuristics

```mermaid
graph TB
A_PY["Python API<br/>python/tvm/topi/utils.py"] --> B_TAGS["Tags<br/>include/tvm/topi/tags.h"]
A_PY --> C_LAYOUT["Layout Helpers<br/>utils.py get_shape()"]
A_PY --> D_INDEX["Index Helpers<br/>utils.py ravel/unravel"]
E_TRANS["Transform Ops<br/>include/tvm/topi/transform.h"] --> F_REGTRANS["Registration<br/>src/topi/transform.cc"]
G_BCAST["Broadcast Ops<br/>include/tvm/topi/broadcast.h"] --> H_REGBCAST["Registration<br/>src/topi/broadcast.cc"]
I_UTILHDR["Detail Utils<br/>include/tvm/topi/detail/*.h"] --> E_TRANS
I_UTILHDR --> G_BCAST
J_UTILPACK["Packed Utils<br/>include/tvm/topi/utils.h"] --> K_UTILREG["Registration<br/>src/topi/utils.cc"]
```

**Diagram sources**
- [utils.py:1-546](file://python/tvm/topi/utils.py#L1-L546)
- [tags.h:24-59](file://include/tvm/topi/tags.h#L24-L59)
- [transform.h:53-800](file://include/tvm/topi/transform.h#L53-L800)
- [broadcast.h:24-497](file://include/tvm/topi/broadcast.h#L24-L497)
- [tensor_utils.h:24-148](file://include/tvm/topi/detail/tensor_utils.h#L24-L148)
- [ravel_unravel.h:24-84](file://include/tvm/topi/detail/ravel_unravel.h#L24-L84)
- [strided_slice.h:24-155](file://include/tvm/topi/detail/strided_slice.h#L24-L155)
- [utils.h:24-51](file://include/tvm/topi/utils.h#L24-L51)
- [transform.cc:24-280](file://src/topi/transform.cc#L24-L280)
- [broadcast.cc:24-87](file://src/topi/broadcast.cc#L24-L87)
- [utils.cc:24-53](file://src/topi/utils.cc#L24-L53)

## Detailed Component Analysis

### Operator Tagging Mechanisms
- Purpose: Provide semantic tags for operations to guide scheduling and fusion
- Constants: Elementwise, injective, broadcast, matmul, convolutions, einsum
- Predicates: is_broadcast and is_injective classify tags for pass decisions
- Usage: Many transform and broadcast functions accept a tag parameter and default to appropriate tags

```mermaid
classDiagram
class Tags {
+string kElementWise
+string kInjective
+string kCommReduce
+string kCommReduceIdx
+string kBroadcast
+string kMatMul
+string kConv2dNCHW
+string kConv2dHWCN
+string kDepthwiseConv2dNCHW
+string kDepthwiseConv2dNHWC
+string kDepthwiseConv2dBackInputNHWC
+string kDepthwiseConv2dBackWeightNHWC
+string kEinsum
+string kGroupConv2d
+is_broadcast(tag) bool
+is_injective(tag) bool
}
```

**Diagram sources**
- [tags.h:24-59](file://include/tvm/topi/tags.h#L24-L59)

**Section sources**
- [tags.h:24-59](file://include/tvm/topi/tags.h#L24-L59)

### Shape Manipulation Utilities
- Empty shape detection: Determines if any dimension is zero-sized
- Ravel/unravel index helpers: Convert between multi-dimensional indices and flat indices
- Strided slice canonicalization: Normalize begin/end/stride and compute output shapes for static/dynamic slices
- Layout shape inference: Given source and destination layouts, infer destination shape using bijective mapping

```mermaid
flowchart TD
Start(["Entry"]) --> CheckEmpty["Check for zero-sized dimensions"]
CheckEmpty --> IsEmpty{"Empty shape?"}
IsEmpty --> |Yes| ReturnEmpty["Return empty tensor path"]
IsEmpty --> |No| ComputeRavel["Compute flat index from multi-dim indices"]
ComputeRavel --> ComputeUnravel["Compute multi-dim indices from flat index"]
ComputeUnravel --> SliceCanon["Canonicalize slice params and compute output shape"]
SliceCanon --> End(["Exit"])
ReturnEmpty --> End
```

**Diagram sources**
- [tensor_utils.h:43-54](file://include/tvm/topi/detail/tensor_utils.h#L43-L54)
- [ravel_unravel.h:45-78](file://include/tvm/topi/detail/ravel_unravel.h#L45-L78)
- [strided_slice.h:91-149](file://include/tvm/topi/detail/strided_slice.h#L91-L149)

**Section sources**
- [utils.h:35-47](file://include/tvm/topi/utils.h#L35-L47)
- [tensor_utils.h:43-148](file://include/tvm/topi/detail/tensor_utils.h#L43-L148)
- [ravel_unravel.h:45-78](file://include/tvm/topi/detail/ravel_unravel.h#L45-L78)
- [strided_slice.h:91-149](file://include/tvm/topi/detail/strided_slice.h#L91-L149)
- [utils.py:510-524](file://python/tvm/topi/utils.py#L510-L524)
- [utils.py:406-440](file://python/tvm/topi/utils.py#L406-L440)

### Tensor Transformation Helpers
- Expand dims: Insert new axes of length 1 at a given axis
- Transpose: Permute dimensions with optional axis list
- Reshape: Change shape using ravel/unravel index mapping
- Squeeze: Remove axes of length 1 with optional at-least-1D enforcement
- Concatenate/Stack: Join tensors along existing/new axes
- Split: Partition along an axis at given indices
- Sliding window: Create windows over specified axes with strides
- Dynamic strided slice: Support mixed static/dynamic begin/end/stride and axis selection

```mermaid
sequenceDiagram
participant U as "User Code"
participant T as "Transform API<br/>transform.h"
participant D as "Detail Utils<br/>detail/*.h"
participant R as "Registration<br/>transform.cc"
U->>T : Call reshape(tensor, newshape)
T->>D : Check empty shape and ravel/unravel helpers
T-->>U : Return transformed tensor
U->>R : Invoke packed "topi.reshape"
R->>T : Dispatch to reshape(...)
T-->>R : Return tensor
R-->>U : Packed result
```

**Diagram sources**
- [transform.h:330-354](file://include/tvm/topi/transform.h#L330-L354)
- [transform.cc:64-67](file://src/topi/transform.cc#L64-L67)
- [ravel_unravel.h:45-78](file://include/tvm/topi/detail/ravel_unravel.h#L45-L78)
- [tensor_utils.h:43-54](file://include/tvm/topi/detail/tensor_utils.h#L43-L54)

**Section sources**
- [transform.h:156-192](file://include/tvm/topi/transform.h#L156-L192)
- [transform.h:205-249](file://include/tvm/topi/transform.h#L205-L249)
- [transform.h:330-354](file://include/tvm/topi/transform.h#L330-L354)
- [transform.h:415-469](file://include/tvm/topi/transform.h#L415-L469)
- [transform.h:481-529](file://include/tvm/topi/transform.h#L481-L529)
- [transform.h:541-573](file://include/tvm/topi/transform.h#L541-L573)
- [transform.h:587-650](file://include/tvm/topi/transform.h#L587-L650)
- [transform.h:716-757](file://include/tvm/topi/transform.h#L716-L757)
- [transform.cc:64-104](file://src/topi/transform.cc#L64-L104)

### Broadcast Operations and Generic Elementwise Patterns
- Broadcast-to: Expand a tensor to a compatible shape using shape compatibility rules
- Auto-broadcasting elementwise ops: Add, subtract, multiply, divide, logical/bitwise comparisons, shifts, power, mod/floordiv/trunc_div, maximum/minimum, log_add_exp
- Overloads support tensor-tensor, tensor-scalar, and scalar-tensor variants

```mermaid
classDiagram
class BroadcastOps {
+broadcast_to(t, output_shape) Tensor
+add(A,B) Tensor
+subtract(A,B) Tensor
+multiply(A,B) Tensor
+divide(A,B) Tensor
+floor_divide(A,B) Tensor
+trunc_divide(A,B) Tensor
+mod(A,B) Tensor
+floor_mod(A,B) Tensor
+maximum(A,B) Tensor
+minimum(A,B) Tensor
+power(A,B) Tensor
+left_shift(A,B) Tensor
+right_shift(A,B) Tensor
+logical_and(A,B) Tensor
+logical_or(A,B) Tensor
+logical_xor(A,B) Tensor
+bitwise_and(A,B) Tensor
+bitwise_or(A,B) Tensor
+bitwise_xor(A,B) Tensor
+greater(A,B) Tensor
+less(A,B) Tensor
+equal(A,B) Tensor
+not_equal(A,B) Tensor
+greater_equal(A,B) Tensor
+less_equal(A,B) Tensor
}
```

**Diagram sources**
- [broadcast.h:48-70](file://include/tvm/topi/broadcast.h#L48-L70)
- [broadcast.h:116-491](file://include/tvm/topi/broadcast.h#L116-L491)

**Section sources**
- [broadcast.h:48-70](file://include/tvm/topi/broadcast.h#L48-L70)
- [broadcast.h:116-491](file://include/tvm/topi/broadcast.h#L116-L491)
- [broadcast.cc:50-83](file://src/topi/broadcast.cc#L50-L83)

### Layout Conversions and Dimension Handling
- Layout shape inference: Given source and destination layouts, compute destination shape using bijective mapping
- Index canonicalization: Normalize indices for strided slices and dynamic indexing
- Dynamic shape detection: Identify dynamic sizes for scheduling and shape inference

```mermaid
sequenceDiagram
participant U as "User Code"
participant L as "Layout API<br/>utils.py"
participant BL as "Bijective Layout<br/>tvm.s_tir"
participant R as "Registration<br/>transform.cc"
U->>L : get_shape(src_shape, src_layout, dst_layout)
L->>BL : bijective_layout(src_layout, dst_layout)
BL-->>L : forward_index mapping
L-->>U : dst_shape
U->>R : Invoke packed "topi.layout_transform"
R->>T : Dispatch to layout_transform(...)
T-->>R : Return tensor
R-->>U : Packed result
```

**Diagram sources**
- [utils.py:406-440](file://python/tvm/topi/utils.py#L406-L440)
- [transform.cc:105-110](file://src/topi/transform.cc#L105-L110)

**Section sources**
- [utils.py:406-440](file://python/tvm/topi/utils.py#L406-L440)
- [utils.py:543-546](file://python/tvm/topi/utils.py#L543-L546)
- [transform.cc:105-110](file://src/topi/transform.cc#L105-L110)

### Packed Function Registration and Integration
- Registration bridges expose TOP-I functions as packed functions for dynamic invocation
- Examples include expand_dims, transpose, reshape, squeeze, concatenate, stack, strided slice, take, matmul, tensordot, and layout transform
- These bridges ensure Python APIs integrate seamlessly with downstream consumers and JIT compilation

```mermaid
sequenceDiagram
participant P as "Python API"
participant REG as "Registration Bridge"
participant CORE as "Core Implementation"
P->>REG : Call topi.reshape(...)
REG->>CORE : Dispatch to reshape(...)
CORE-->>REG : Return tensor
REG-->>P : Packed result
```

**Diagram sources**
- [transform.cc:40-280](file://src/topi/transform.cc#L40-L280)
- [broadcast.cc:50-87](file://src/topi/broadcast.cc#L50-L87)
- [utils.cc:31-53](file://src/topi/utils.cc#L31-L53)

**Section sources**
- [transform.cc:40-280](file://src/topi/transform.cc#L40-L280)
- [broadcast.cc:50-87](file://src/topi/broadcast.cc#L50-L87)
- [utils.cc:31-53](file://src/topi/utils.cc#L31-L53)

## Dependency Analysis
- Python layer depends on TVM IR and scheduling primitives; it also imports C++ utilities via the cpp module
- C++ headers depend on TVM TE, TIRX, and S-TIR layout utilities
- Registration bridges depend on reflection registry and packed function dispatch
- Tagging system is consumed by transforms and broadcasts to annotate operations

```mermaid
graph LR
PY["python/tvm/topi/utils.py"] --> IR["tvm.ir / tvm.te / tvm.tirx"]
PY --> TAGS["include/tvm/topi/tags.h"]
PY --> DETAIL["include/tvm/topi/detail/*.h"]
DETAIL --> IR
TRANS["include/tvm/topi/transform.h"] --> DETAIL
BC["include/tvm/topi/broadcast.h"] --> DETAIL
REGTRANS["src/topi/transform.cc"] --> TRANS
REGBCAST["src/topi/broadcast.cc"] --> BC
REGUTIL["src/topi/utils.cc"] --> UTILHDR["include/tvm/topi/utils.h"]
```

**Diagram sources**
- [utils.py:20-31](file://python/tvm/topi/utils.py#L20-L31)
- [tags.h:24-59](file://include/tvm/topi/tags.h#L24-L59)
- [transform.h:27-52](file://include/tvm/topi/transform.h#L27-L52)
- [broadcast.h:27-30](file://include/tvm/topi/broadcast.h#L27-L30)
- [transform.cc:24-33](file://src/topi/transform.cc#L24-L33)
- [broadcast.cc:24-28](file://src/topi/broadcast.cc#L24-L28)
- [utils.cc:24-29](file://src/topi/utils.cc#L24-L29)

**Section sources**
- [utils.py:20-31](file://python/tvm/topi/utils.py#L20-L31)
- [transform.h:27-52](file://include/tvm/topi/transform.h#L27-L52)
- [broadcast.h:27-30](file://include/tvm/topi/broadcast.h#L27-L30)
- [transform.cc:24-33](file://src/topi/transform.cc#L24-L33)
- [broadcast.cc:24-28](file://src/topi/broadcast.cc#L24-L28)
- [utils.cc:24-29](file://src/topi/utils.cc#L24-L29)

## Performance Considerations
- Prefer injective and broadcast tags for operations that map directly to elementwise or broadcasting patterns to enable efficient scheduling and fusion
- Use canonicalized indices and static shape checks where possible to reduce dynamic overhead
- Leverage layout-aware transformations and shape inference to avoid unnecessary copies and promote contiguous memory access
- Utilize packed-function registration to minimize Python overhead during JIT compilation and runtime dispatch

## Troubleshooting Guide
- Invalid shape errors: Some functions raise exceptions for incompatible shapes or unsupported configurations; validate inputs and ensure shapes meet operator requirements
- Dynamic vs static indexing: When mixing static and dynamic indices, ensure canonicalization and output shape computation are handled consistently
- Layout mismatches: When inferring destination shapes, ensure source and destination layouts are compatible and bijectively mappable

**Section sources**
- [utils.py:33-35](file://python/tvm/topi/utils.py#L33-L35)
- [strided_slice.h:119-149](file://include/tvm/topi/detail/strided_slice.h#L119-L149)
- [utils.py:434-439](file://python/tvm/topi/utils.py#L434-L439)

## Conclusion
TOP-I utilities provide a cohesive toolkit for shape manipulation, tensor transformations, operator tagging, and generic operation implementations. By leveraging tagging semantics, layout-aware helpers, and packed-function registration, developers can implement custom operators efficiently and integrate them into the broader TVM ecosystem. Following best practices for code reuse, dimension handling, and performance optimization ensures robust and maintainable operator development.