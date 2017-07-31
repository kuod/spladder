import pysam
import re
import scipy as sp
import scipy.sparse
import copy
import pdb
import time
import h5py

if __package__ is None:
    __package__ = 'modules'

from .utils import *
from .init import *

def get_reads(fname, chr_name, start, stop, strand = None, filter = None, mapped = True, spliced = True, var_aware = None, collapse = False, primary_only=False, no_mm=False):
    
    infile = pysam.Samfile(fname, 'rb')

    ### vectors to build sparse matrix
    i = []
    j = []

    read_cnt = 0
    introns = []
    if collapse:
        read_matrix = sp.zeros((1, stop - start), dtype='int')
    else:
        read_matrix = scipy.sparse.coo_matrix((sp.ones(0), ([], [])), shape = (0, stop - start), dtype='bool')

    length = stop - start

    #print >> sys.stderr, 'querying %s:%i-%i' % (chr_name, start, stop)
    ### TODO THIS IS A HACK
    if chr_name == 'MT':
        return (read_matrix, sp.zeros((0, 2), dtype='int'))

    if infile.gettid(chr_name) > -1:
        ### pysam query is zero based in position (results are as well), all intervals are pythonic half open
        for read in infile.fetch(chr_name, start, stop, until_eof=True):
            
            ### check if we skip this reads
            if filter_read(read, filter, spliced, mapped, strand, primary_only, var_aware, no_mm):
                continue

            ### get introns and covergae
            p = read.pos 
            for o in read.cigar:
                if o[0] == 3:
                    introns.append([p, p + o[1]])
                if o[0] in [0, 2]:
                    _start = int(max(p-start, 0))
                    _stop = int(min(p + o[1] - start, stop - start))
                    if _stop < 0 or _start > length:
                        if o[0] in [0, 2, 3]:
                            p += o[1]
                        continue
                    if collapse:
                        read_matrix[0, _start:_stop] += 1
                    else:
                        r = list(range(_start, _stop))
                        i.extend([read_cnt] * len(r))
                        j.extend(r)
                        #for pp in range(p, p + o[1]):
                        #    if pp - start >= 0 and pp < stop:
                        #        i.append(read_cnt)
                        #        j.append(pp - start)
                if o[0] in [0, 2, 3]:
                    p += o[1]

            ### the follwoing is new behavior and gonne come in the next version --> deletions are not counted towards coverage
            #### get coverage
            #for p in read.positions:
            #    if p - start >= 0:
            #        if p >= stop:
            #            break
            #        else:
            #            i.append(read_cnt)
            #            j.append(p - start)

            read_cnt += 1

        ### construct sparse matrix
        if not collapse:
            try:
                i = sp.array(i, dtype='int')
                j = sp.array(j, dtype='int')
                read_matrix = scipy.sparse.coo_matrix((sp.ones(i.shape[0]), (i, j)), shape = (read_cnt, stop - start), dtype='bool')
            except ValueError:
                step = 1000000
                _k = step
                assert len(i) > _k
                read_matrix = scipy.sparse.coo_matrix((sp.ones(_k), (i[:_k], j[:_k])), shape = (read_cnt, stop - start), dtype='bool')
                while _k < len(i):
                    _l = min(len(i), _k + step)
                    read_matrix += scipy.sparse.coo_matrix((sp.ones(_l - _k), (i[_k:_l], j[_k:_l])), shape = (read_cnt, stop - start), dtype='bool')                
                    _k = _l

    ### construct introns
    if len(introns) > 0:
        introns = sp.array(introns, dtype='int')
    else:
        introns = sp.zeros((0, 2), dtype='int')

    #if collapse:
    #    return (read_matrix.sum(axis = 0), introns)
    #else:
    return (read_matrix, introns)


def add_reads_from_bam(blocks, filenames, types, filter=None, var_aware=False, primary_only=False, no_mm=False):
    # blocks coordinates are assumed to be in closed intervals

    #if filter is None:
    #    filter = dict()
    #    filter['intron'] = 20000
    #    filter['exon_len'] = 8
    #    filter['mismatch']= 1

    if not types: 
        print('add_reads_from_bam: nothing to do')
        return

    verbose = False
    pair = False

    pair = ('pair_coverage' in types)
    clipped = False

    if type(blocks).__module__ != 'numpy':
        blocks = sp.array([blocks])

    for b in range(blocks.shape[0]):

        introns = None

        if verbose and  b % 10 == 0:
            print('\radd_exon_track_from_bam: %i(%i)' % (b, blocks.shape[0]))
        block_len = int(blocks[b].stop - blocks[b].start)

        ## get data from bam
        if 'exon_track' in types:
            (introns, coverage) = get_all_data(blocks[b], filenames, filter=filter, var_aware=var_aware, primary_only=primary_only, no_mm=no_mm) 
        if 'mapped_exon_track' in types:
            (introns, mapped_coverage) = get_all_data(blocks[b], filenames, spliced=False, filter=filter, var_aware=var_aware, primary_only=primary_only, no_mm=no_mm) 
        if 'spliced_exon_track' in types:
            (introns, spliced_coverage) = get_all_data(blocks[b], filenames, mapped=False, filter=filter, var_aware=var_aware, primary_only=primary_only, no_mm=no_mm) 
        if 'polya_signal_track' in types:
            (introns, polya_signals) = get_all_data_uncollapsed(blocks[b], filenames, filter=filter, clipped=True, var_aware=var_aware, primary_only=primary_only, no_mm=no_mm)
        if 'end_signal_track' in types:
            (introns, read_end_signals) = get_all_data_uncollapsed(blocks[b], filenames, filter=filter, var_aware=var_aware, primary_only=primary_only, no_mm=no_mm)

        if 'intron_list' in types or 'intron_track' in types:
            if introns is None:
                (introns, spliced_coverage) = get_all_data(blocks[b], filenames, mapped=False, filter=filter, var_aware=var_aware, primary_only=primary_only, no_mm=no_mm)
        if not introns is None:
            introns = sort_rows(introns)

        # add requested data to block
        tracks = sp.zeros((0, block_len))
        intron_list = []
        for ttype in types:
            ## add exon track to block
            ##############################################################################
            if ttype == 'exon_track':
                tracks = sp.r_[tracks, coverage] 
            ## add mapped exon track to block
            ##############################################################################
            elif ttype == 'mapped_exon_track':
                tracks = sp.r_[tracks, mapped_coverage] 
            ## add spliced exon track to block
            ##############################################################################
            elif ttype == 'spliced_exon_track':
                tracks = sp.r_[tracks, spliced_coverage] 
            ## add intron coverage track to block
            ##############################################################################
            elif ttype == 'intron_track':
                intron_coverage = sp.zeros((1, block_len))
                if introns.shape[0] > 0:
                    for k in range(introns.shape[0]):
                        from_pos = max(0, introns[k, 0])
                        to_pos = min(block_len, intron_list[k, 1])
                        intron_coverage[from_pos:to_pos] += 1
                tracks = sp.r_[tracks, intron_coverage] 
            ## compute intron list
            ##############################################################################
            elif ttype == 'intron_list':
                if introns.shape[0] > 0:
                    ### compute number of occurences
                    introns = sort_rows(introns)
                    num_introns = introns.shape[0]
                    (introns, fidx) = unique_rows(introns, index=True)
                    lidx = sp.r_[fidx[1:] - 1, num_introns - 1]
                    introns = sp.c_[introns, lidx - fidx + 1]

                    ### filter introns for location relative to block
                    ### this is legacy behavior for matlab versions!
                    ### TODO - Think about keeping this? Make it a parameter?
                    k_idx = sp.where((introns[:, 0] > blocks[0].start) & (introns[:, 1] < blocks[0].stop))[0]
                    introns = introns[k_idx, :]

                    if introns.shape[0] > 0:
                        s_idx = sp.argsort(introns[:, 0])
                        introns = introns[s_idx, :]
                    
                    #if not filter is None and 'mincount' in filter:
                    if filter is not None and 'mincount' in filter:
                        take_idx = sp.where(introns[:, 2] >= filter['mincount'])[0]
                        if take_idx.shape[0] > 0:
                            intron_list.append(introns[take_idx, :])
                        else:
                            intron_list.append(sp.zeros((0, 3), dtype='int'))
                    else:
                        intron_list.append(introns)
                else:
                    intron_list.append(sp.zeros((0, 3), dtype='int'))
            ## add polya signal track
            ##############################################################################
            elif ttype == 'polya_signal_track':
                ### get only end positions of reads
                shp = polya_signals
                end_idx = shp[0] - 1 - polya_signals[:, ::-1].argmax(axis = 1)
                polya_signals = scipy.sparse.coo_matrix((sp.ones((shp[1],)), (sp.array(list(range(shp[1]))), end_idx)), shape = shp)
                tracks = sp.r_[tracks, polya_signals.sum(axis = 0)]
            ## add end signal track
            ##############################################################################
            elif ttype == 'end_signal_track':
                ### get only end positions of reads
                shp = end_signals
                end_idx = shp[0] - 1 - end_signals[:, ::-1].argmax(axis = 1)
                end_signals = scipy.sparse.coo_matrix((sp.ones((shp[1],)), (sp.array(list(range(shp[1]))), end_idx)), shape = shp)
                tracks = sp.r_[tracks, end_signals.sum(axis = 0)]
            else: 
                print('ERROR: unknown type of data requested: %s' % ttype, file=sys.stderr)
    
    if len(types) == 1 and types[0] == 'intron_list':
        return intron_list
    elif 'intron_list' in types:
        return (tracks, intron_list)
    else:
        return tracks

def add_reads_from_sparse_bam(gg, fname, contig, types=None, filter=None, cache=None, unstranded=False):

    if cache is None or len(cache) == 0:
        ### load counts from summary file
        if fname.endswith('hdf5'):
            IN = h5py.File(fname, 'r')
        else:
            if not filter is None:
                IN = h5py.File(re.sub(r'bam$', '', fname) + 'filt.' + 'hdf5', 'r')
            else:
                IN = h5py.File(re.sub(r'bam$', '', fname) + 'hdf5')

        ### re-build sparse matrix
        cache['reads'] = scipy.sparse.coo_matrix((IN[contig + '_reads_dat'][:], (IN[contig + '_reads_row'][:], IN[contig + '_reads_col'][:])), shape=IN[contig + '_reads_shp'][:], dtype='uint32').tocsc()
        cache['introns_m'] = IN[contig + '_introns_m'][:]
        cache['introns_p'] = IN[contig + '_introns_p'][:]

        IN.close()

    ret = []
    if 'exon_track' in types:
        if cache['reads'].shape[0] == 0:
            tracks = sp.zeros((1, gg.stop - gg.start), dtype='int')
        elif not unstranded and cache['reads'].shape[0] > 1:
            tracks = cache['reads'][[0, 1 + int(gg.strand == '-')], gg.start:gg.stop].todense() 
        else:
            tracks = cache['reads'][:, gg.start:gg.stop].todense() 
        ret.append(tracks)

    if 'intron_list' in types:
        if unstranded:
            intron_list = sp.r_[get_intron_range(cache['introns_p'], gg.start, gg.stop), get_intron_range(cache['introns_m'], gg.start, gg.stop)]
        else:
            if (cache['introns_m'].shape[0] > 0) and (gg.strand == '-'):
                intron_list = get_intron_range(cache['introns_m'], gg.start, gg.stop)
            else:
                intron_list = get_intron_range(cache['introns_p'], gg.start, gg.stop)
        if not filter is None and intron_list.shape[0] > 0:
            k_idx = sp.where(intron_list[:, 2] >= filter['mincount'])[0]
            intron_list = intron_list[k_idx, :]
        ret.append(intron_list)

    return ret


#function [introns, coverage, pair_cov] = get_all_data(block, mapped, spliced, filenames, filter, clipped, var_aware) 
def get_all_data(block, filenames, mapped=True, spliced=True, filter=None, clipped=False, var_aware=False, primary_only=False, no_mm=False):

    block_len = block.stop - block.start
    # get all data from bam file
    coverage = sp.zeros((1, block_len))
    introns = None

    if isinstance(filenames, str):
        filenames = [filenames]

    for j in range(len(filenames)):
        fname = filenames[j]
        if not os.path.exists(fname):
            print('add_reads_from_bam: did not find file %s' % fname, file=sys.stderr)
            continue

        ### TODO: implement subsampling, if needed
        contig_name = block.chr
        strand = block.strand
        ### check for filter maps -> requires uncollapsed reads
        if not filter is None and 'maps'in filter:
            collapse = False
        else:
            collapse = True
        ### get reads from bam file
        (coverage_tmp, introns_tmp) = get_reads(fname, contig_name, block.start, block.stop, strand, filter, mapped, spliced, var_aware, collapse, primary_only, no_mm)

        ### compute total coverages
        if not filter is None and 'maps' in filter:
            ### TODO re-implement these filters !!!
            ### apply filters
            if 'repeat_map' in filter['maps']:
                curr_idx = filter['maps']['repeat_map'][block.chr_num][block.start:block.stop]
                keep_idx = sp.where(sp.sum(coverage_tmp[:, ~curr_idx], axis = 1) > 0)[0]
                coverage_tmp = coverage_tmp[keep_idx, :]
            if 'indel_map' in filter['maps']:
                curr_idx = filter['maps']['indel_map'][block.chr_num][block.start:block.stop]
                #rm_idx = curr_idx(max(coverage_tmp, [], 2)' == 0)' == 1;
                keep_idx = ~curr_idx[coverage_tmp.max(axis = 1) == 0]
                coverage_tmp = coverage_tmp[keep_idx, :]
            if 'gene_overlap_map' in filter['maps']:
                curr_idx = filter['maps']['gene_overlap_map'][block.chr_num][block.start:block.stop]
                #rm_idx = sum(coverage_tmp(:, curr_idx)) > 0;
                keep_idx = coverage_tmp[:, curr_idx].sum(axis = 0) == 0
                coverage_tmp = coverage_tmp[keep_idx, :]
            coverage += coverage_tmp.sum(axis = 0)
        else:
            coverage += coverage_tmp

        if introns is None:
            introns = introns_tmp
        else:
            introns = sp.r_[introns, introns_tmp]

    return (introns, coverage)

#function [introns, coverage, pair_cov] = get_all_data_uncollapsed(block, mapped, spliced, filenames, filter, var_aware) 
def get_all_data_uncollapsed(block,filenames, mapped=True, spliced=True, filter=None, var_aware=False, primary_only=False, no_mm=False):

    block_len = block.stop - block.start
    # get all data from bam file
    coverage = sp.zeros((1, block_len))
    introns = None

    for j in range(lenfilenames):
        fname = filenames[j]
        if not ot.path.exists(fname):
            print('add_reads_from_bam: did not find file %s' % fname, file=sys.stderr)
            continue

        ### TODO: implement subsampling, if needed
        contig_name = block.chr
        strand = block.strand
        collapse = False
        (coverage_tmp, introns_tmp) = get_reads(fname, contig_name, block.start, block.stop, strand, filter, mapped, spliced, var_aware, collapse, primary_only, no_mm)
        coverage = sp.r_[coverage, coverage_tmp]

    return (introns, coverage)

def get_intron_list(genes, CFG):

    introns = sp.zeros((genes.shape[0], 2), dtype = 'object')
    introns[:] = None

    ### collect all possible combinations of contigs and strands
    (regions, CFG) = init_regions(CFG['bam_fnames'], CFG)

    ### form chunks for quick sorting
    strands = ['+', '-']

    ### ignore contigs not present in bam files 
    keepidx = sp.where(sp.in1d(sp.array([CFG['chrm_lookup'][x.chr] for x in genes]), sp.array([x.chr_num for x in regions])))[0]
    genes = genes[keepidx]

    c = 0
    num_introns_filtered = 0
    t0 = time.time()

    contigs = sp.array([x.chr for x in genes], dtype='str')
    gene_strands = sp.array([x.strand for x in genes])
    for contig in sp.unique(contigs):
        bam_cache = dict()
        for si, s in enumerate(strands):
            cidx = sp.where((contigs == contig) & (gene_strands == s))[0]

            for i in cidx:

                if CFG['verbose'] and (c+1) % 100 == 0:
                    t1 = time.time()
                    print('%i (%i) genes done (%i introns taken) ... took %i secs' % (c+1, genes.shape[0], num_introns_filtered, t1 - t0), file=sys.stdout)
                    t0 = t1

                gg = sp.array([copy.copy(genes[i])], dtype='object')
                assert(gg[0].strand == s)
                gg[0].start = max(gg[0].start - 5000, 1)
                gg[0].stop = gg[0].stop + 5000
                assert(gg[0].chr == contig)

                if CFG['bam_to_sparse']:
                    if isinstance(CFG['bam_fnames'], str):
                        [intron_list_tmp] = add_reads_from_sparse_bam(gg[0], CFG['bam_fnames'], contig, types=['intron_list'], filter=CFG['read_filter'], cache=bam_cache, unstranded=True)
                    else:
                        intron_list_tmp = None
                        for fname in CFG['bam_fnames']:
                            [tmp_] = add_reads_from_sparse_bam(gg[0], fname, contig, types=['intron_list'], filter=CFG['read_filter'], cache=bam_cache, unstranded=True)
                            if intron_list_tmp is None:
                                intron_list_tmp = tmp_
                            else:
                                intron_list_tmp = sp.r_[intron_list_tmp, tmp_]

                        ### some merging in case of multiple bam files
                        if len(CFG['bam_fnames']) > 1:
                            intron_list_tmp = sort_rows(intron_list_tmp)
                            rm_idx = []
                            for i in range(1, intron_list_tmp.shape[0]):
                                if sp.all(intron_list_tmp[i, :2] == intron_list_tmp[i-1, :2]):
                                    intron_list_tmp[i, 2] += intron_list_tmp[i-1, 2]
                                    rm_idx.append(i-1)
                            if len(rm_idx) > 0:
                                k_idx = sp.setdiff1d(sp.arange(intron_list_tmp.shape[0]), rm_idx)
                                intron_list_tmp = intron_list_tmp[k_idx, :]
                else:
                    [intron_list_tmp] = add_reads_from_bam(gg, CFG['bam_fnames'], ['intron_list'], CFG['read_filter'], CFG['var_aware'], CFG['primary_only'], CFG['ignore_mismatch_tag'])
                num_introns_filtered += intron_list_tmp.shape[0]
                introns[i, si] = sort_rows(intron_list_tmp)

                c += 1
        
    for j in range(introns.shape[0]):
        if introns[j, 0] is None:
            introns[j, 0] = sp.zeros((0, 3), dtype='int')
        if introns[j, 1] is None:
            introns[j, 1] = sp.zeros((0, 3), dtype='int')

    return introns



def filter_read(read, filter, spliced, mapped, strand, primary_only, var_aware, no_mm=False):

    if read.is_unmapped:
        return True
    if primary_only and read.is_secondary:
        return True

    is_spliced = ('N' in read.cigarstring)
    if is_spliced:
        if not spliced:
            return True
    elif not mapped:
        return True

    tags = dict(read.tags)

    if filter is not None:
        ### handle mismatches
        if var_aware:
            if filter['mismatch'] < (tags['XM'] + tags['XG']):
                return True
        else:
            if no_mm:
                return False
            else:
                return filter['mismatch'] < tags['NM']

        if is_spliced:
            ### handle min segment length
            ### remove all elements from CIGAR sting that do not 
            ### contribute to the segments (hard- and softclips and insertions)
            cig = re.sub(r'[0-9]*[HSI]', '', read.cigarstring)
            ### split the string at the introns and sum the remaining segment elements, compare to filter
            if min([sum([int(y) for y in re.split('[MD]', x)[:-1]]) for x in re.split('[0-9]*N', cig)]) <= filter['exon_len']:
                return True
    
    ### check strand information
    if strand is not None:
        try:
            if tags['XS'] != strand:
                return True
        except KeyError:
            pass

    return False


def summarize_chr(fname, chr_name, CFG, filter=None, strand=None, mapped=True, spliced=True):

    infile = pysam.Samfile(fname, 'rb')
    introns_p = dict()
    introns_m = dict()
    stranded = False

    if CFG['verbose']:
        print('Summarizing contig %s of file %s' % (chr_name, fname), file=sys.stdout)

    chr_len = [int(x['LN']) for x in parse_header(infile.text)['SQ'] if x['SN'] == chr_name]
    if len(chr_len) == 0:
        print('No information found for contig %s' % (chr_name), file=sys.stdout)
        return (chr_name, scipy.sparse.coo_matrix(sp.zeros((0, 1)), dtype='uint32'), sp.zeros((0, 3), dtype='uint32'), sp.zeros((0, 3), dtype='uint32'))
    chr_len = chr_len[0]

    ### read matrix has three rows: 0 - no strand info, 1 - plus strand info, 2 - minus strand info
    read_matrix = sp.zeros((3, chr_len), dtype='uint32') 

    if infile.gettid(chr_name) > -1:
        ### pysam query is zero based in position (results are as well), all intervals are pythonic half open
        for read in infile.fetch(chr_name, until_eof=True):

            ### check if we skip this reads
            if filter_read(read, filter, spliced, mapped, strand, CFG['primary_only'], CFG['var_aware']):
                continue
            
            tags = dict(read.tags)
            curr_read_stranded = ('XS' in tags)
            is_minus = False
            if curr_read_stranded:
                stranded = True
                is_minus = (tags['XS'] == '-')
                    
            ### get introns and covergae
            p = read.pos 
            for o in read.cigar:
                if o[0] == 3:
                    if is_minus:
                        try:
                            introns_m[(p, p + o[1])] += 1
                        except KeyError:
                            introns_m[(p, p + o[1])] = 1
                    else:
                        try:
                            introns_p[(p, p + o[1])] += 1
                        except KeyError:
                            introns_p[(p, p + o[1])] = 1
                if o[0] in [0, 2]:
                    read_matrix[0 + int(curr_read_stranded) + int(is_minus), p:(p + o[1])] += 1
                if o[0] in [0, 2, 3]:
                    p += o[1]

    ### convert introns into scipy array
    if len(introns_p) >= 1:
        introns_p = sp.array([[k[0], k[1], v] for k, v in introns_p.items()], dtype='uint32')
        introns_p = sort_rows(introns_p)
    else:
        introns_p = sp.zeros(shape=(0, 3), dtype='uint32')
    if len(introns_m) >= 1:
        introns_m = sp.array([[k[0], k[1], v] for k, v in introns_m.items()], dtype='uint32')
        introns_m = sort_rows(introns_m)
    else:
        introns_m = sp.zeros(shape=(0, 3), dtype='uint32')
    ### make read matrix sparse
    if not stranded:
        read_matrix = scipy.sparse.coo_matrix(read_matrix[[0], :], dtype='uint32')
    else:
        read_matrix = scipy.sparse.coo_matrix(read_matrix, dtype='uint32')

    return (chr_name, read_matrix, introns_m, introns_p)


def get_intron_range(introns, start, stop):
    """Given a sorted list of introns, return the subset of introns that
       overlaps that start stop interval"""

    if introns.shape[0] == 0:
        return introns

    idx = sp.where((introns[:, 0] > start) & (introns[:, 1] < stop))[0]

    # TODO: Decide whether we would like to allow introns that span over the full gene
    #idx = sp.where(((introns[:, 0] < stop) & (introns[:, 1] > start)) |
    #               ((introns[:, 0] < start) & (introns[:, 1] > stop)))[0]

    return introns[idx, :]

