import h5py
import pandas

def get_nested_dict_from_shot(filepath):
    with h5py.File(filepath,'r') as h5_file:
        row = dict(h5_file['globals'].attrs)
        if 'results' in h5_file:
            for groupname in h5_file['results']:
                resultsgroup = h5_file['results'][groupname]
                row[groupname] = dict(resultsgroup.attrs)
        if 'images' in h5_file:
            for orientation in h5_file['images'].keys():
                row[orientation] = dict(h5_file['images'][orientation].attrs)
                for label in h5_file['images'][orientation]:
                    row[orientation][label] = {}
                    group = h5_file['images'][orientation][label]
                    for image in group:
                        row[orientation][label][image] = dict(
                            group[image].attrs)
        row['filepath'] = filepath
        row['labscript'] = h5_file['script'].attrs['name']
        row['run time'] = h5_file.attrs['run time']
        row['run number'] = h5_file.attrs['run number']
        return row
            
def flatten_dict(dictionary, keys=tuple()):
    """Takes a nested dictionary whose keys are strings, and returns a
    flat dictionary whose keys are tuples of strings, each element of
    which is the key for one level of the hierarchy."""
    result = {}
    for name in dictionary:
        if isinstance(dictionary[name],dict):
            flat = flatten_dict(dictionary[name],keys=keys + (str(name),))
            result.update(flat)
        else:
            result[keys + (str(name),)] = dictionary[name]
    return result
            
def flat_dict_to_hierarchical_dataframe(dictionary):
    """Make all the keys tuples of the same length"""
    max_tuple_length = 2 # Must have at least two levels to make a MultiIndex
    for key in dictionary:
        max_tuple_length = max(max_tuple_length,len(key))
    result = {}
    for key in dictionary:
        newkey = key[:]
        while len(newkey) < max_tuple_length:
            newkey += ('',)
        result[newkey] = dictionary[key]    
    index = pandas.MultiIndex.from_tuples(sorted(result.keys()))
    return pandas.DataFrame([result],columns=index)  
      
def get_dataframe_from_shot(filepath):
    nested_dict = get_nested_dict_from_shot(filepath)
    flat_dict =  flatten_dict(nested_dict)
    df = flat_dict_to_hierarchical_dataframe(flat_dict)
    return df
    
def pad_columns(df, n):
    """Add depth to hiererchical column labels with empty strings"""
    if df.columns.nlevels == n:
        return df
    new_columns = []
    data = {}
    for column in df.columns:
        new_column = column + ('',)*(n-len(column))
        new_columns.append(new_column)
        data[new_column] = df[column]
    index = pandas.MultiIndex.from_tuples(new_columns)
    return pandas.DataFrame(data,columns = index)

def concat_with_padding(df1, df2):
    """Concatenates two dataframes with MultiIndex column labels,
    padding the shallower hierarchy such that the two MultiIndexes have
    the same nlevels."""
    if df1.columns.nlevels < df2.columns.nlevels:
        df1 = pad_columns(df1, df2.columns.nlevels)
    elif df1.columns.nlevels > df2.columns.nlevels:
        df2 = pad_columns(df2, df1.columns.nlevels)
    return df1.append(df2, ignore_index=True)
    
def replace_with_padding(df,row,index):
    if df.columns.nlevels < row.columns.nlevels:
        df = pad_columns(df, row.columns.nlevels)
    elif df.columns.nlevels > row.columns.nlevels:
        row = pad_columns(row, df.columns.nlevels)
    # Wow, changing the index of a single row dataframe is a pain in
    # the neck:
    row = pandas.DataFrame(row.ix[0],columns=[index]).T
    # Wow, replacing a row of a dataframe is a pain in the neck:
    df = df.drop([index])
    df = df.append(row)
    df = df.sort()
    return df
