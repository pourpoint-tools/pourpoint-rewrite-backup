"""Release unused dictionary values in already-gathered Arrow selections."""

import pyarrow as pa
import pyarrow.compute as pc


def _compact_dictionary_values(array: pa.Array) -> pa.Array:
    if isinstance(array, pa.DictionaryArray):
        # Sorted original indices preserve the meaning of ordered dictionaries.
        used = pc.call_function("unique", [pc.call_function("drop_null", [array.indices])])
        used = pc.take(used, pc.call_function("array_sort_indices", [used]))
        indices = pc.cast(
            pc.call_function("index_in", [array.indices], options=pc.SetLookupOptions(used)), array.type.index_type
        )
        values = _compact_dictionary_values(pc.take(array.dictionary, used))
        return pa.DictionaryArray.from_arrays(indices, values, ordered=array.type.ordered)
    if isinstance(array, pa.ExtensionArray):
        return pa.ExtensionArray.from_storage(array.type, _compact_dictionary_values(array.storage))
    children: list[pa.Array]
    if pa.types.is_struct(array.type) or pa.types.is_union(array.type):
        children = [array.field(i) for i in range(array.type.num_fields)]
    elif (
        pa.types.is_list(array.type)
        or pa.types.is_large_list(array.type)
        or pa.types.is_fixed_size_list(array.type)
        or pa.types.is_map(array.type)
        or pa.types.is_list_view(array.type)
        or pa.types.is_large_list_view(array.type)
    ):
        children = [array.values]
    elif pa.types.is_run_end_encoded(array.type):
        children = [array.run_ends, array.values]
    else:
        return array
    return pa.Array.from_buffers(
        array.type,
        len(array),
        array.buffers()[: array.type.num_buffers],
        null_count=array.null_count,
        offset=array.offset,
        children=[_compact_dictionary_values(child) for child in children],
    )


def compact_dictionaries(table: pa.Table) -> pa.Table:
    """Keep exact schema and selected values without unreferenced dictionary data.

    Call after Arrow take/filter has gathered ordinary value buffers. Arrow's
    gather kernels retain dictionaries even when only one entry is selected.
    """
    return pa.Table.from_arrays(
        [
            pa.chunked_array([_compact_dictionary_values(chunk) for chunk in column.chunks], type=column.type)
            for column in table.columns
        ],
        schema=table.schema,
    )
