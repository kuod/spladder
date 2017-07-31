if __package__ is None:
    __package__ ='modules'

import pickle
import math
import h5py
import scipy as sp
import re
import pdb

from .classes.segmentgraph import Segmentgraph
from .classes.counts import Counts
from .reads import *
from .hdf5 import appendToHDF5
from . import rproc as rp

def count_graph_coverage(genes, fn_bam=None, CFG=None, fn_out=None):
# [counts] = count_graph_coverage(genes, fn_bam, CFG, fn_out)

    if fn_bam is None and isinstance(genes, dict):
        PAR = genes
        genes = PAR['genes']
        fn_bam = PAR['fn_bam']
        if 'fn_out' in PAR:
            fn_out = PAR['fn_out'] 
        CFG = PAR['CFG']

    if not isinstance(fn_bam, list):
        fn_bam = [fn_bam]
    counts = sp.zeros((len(fn_bam), genes.shape[0]), dtype='object')

    intron_tol = 0 

    sys.stdout.write('genes: %i\n' % genes.shape[0])
    for f in range(counts.shape[0]):
        sys.stdout.write('\nsample %i/%i\n' % (f + 1, counts.shape[0])) 

        ### iterate over all genes and generate counts for
        ### the segments in the segment graph
        ### and the splice junctions in the splice graph
        ### iterate per contig, so the bam caching works better
        contigs = sp.array([x.chr for x in genes])
        for contig in sp.unique(contigs):
            contig_idx = sp.where(contigs == contig)[0]
            bam_cache = dict()
            print('\ncounting %i genes on contig %s' % (contig_idx.shape[0], contig))
            for ii,i in enumerate(contig_idx):
                sys.stdout.write('.')
                if ii > 0 and ii % 50 == 0:
                    sys.stdout.write('%i/%i\n' % (ii, contig_idx.shape[0]))
                sys.stdout.flush()
                gg = genes[i]
                if gg.segmentgraph.is_empty():
                    gg.segmentgraph = Segmentgraph(gg)
                gg.start = gg.segmentgraph.segments.ravel().min()
                gg.stop = gg.segmentgraph.segments.ravel().max()

                counts[f, i] = Counts(gg.segmentgraph.segments.shape[1])

                if CFG['bam_to_sparse'] and (fn_bam[f].endswith('npz') or os.path.exists(re.sub(r'bam$', '', fn_bam[f]) + 'npz')):
                    ### make sure that we query the right contig from cache
                    assert(gg.chr == contig)
                    (tracks, intron_list) = add_reads_from_sparse_bam(gg, fn_bam[f], contig, types=['exon_track','intron_list'], filter=None, cache=bam_cache)
                else:
                    ### add RNA-seq evidence to the gene structure
                    (tracks, intron_list) = add_reads_from_bam(gg, fn_bam[f], ['exon_track','intron_list'], None, CFG['var_aware'], CFG['primary_only']);
                    intron_list = intron_list[0] ### TODO

                ### extract mean exon coverage for all segments
                for j in range(gg.segmentgraph.segments.shape[1]):
                    idx = sp.arange(gg.segmentgraph.segments[0, j], gg.segmentgraph.segments[1, j]) - gg.start
                    counts[f, i].segments[j] = sp.mean(sp.sum(tracks[:, idx], axis=0))
                    counts[f, i].seg_pos[j] = sp.sum(sp.sum(tracks[:, idx], axis=0) > 0)

                k, l = sp.where(gg.segmentgraph.seg_edges == 1)

                ### there are no introns to count
                if intron_list.shape[0] == 0:
                    for m in range(k.shape[0]):
                        if counts[f, i].edges.shape[0] == 0:
                            counts[f, i].edges = sp.atleast_2d(sp.array([sp.ravel_multi_index([k[m], l[m]], gg.segmentgraph.seg_edges.shape), 0]))
                        else:
                            counts[f, i].edges = sp.r_[counts[f, i].edges, sp.atleast_2d(sp.array([sp.ravel_multi_index([k[m], l[m]], gg.segmentgraph.seg_edges.shape), 0]))]
                    continue

                ### extract intron counts 
                for m in range(k.shape[0]):
                    idx = sp.where((sp.absolute(intron_list[:, 0] - gg.segmentgraph.segments[1, k[m]]) <= intron_tol) & (sp.absolute(intron_list[:, 1] - gg.segmentgraph.segments[0, l[m]]) <= intron_tol))[0]
                    if counts[f, i].edges.shape[0] == 0:
                        if idx.shape[0] > 0:
                            counts[f, i].edges = sp.atleast_2d(sp.array([sp.ravel_multi_index([k[m], l[m]], gg.segmentgraph.seg_edges.shape), sp.sum(intron_list[idx, 2])]))
                        else:
                            counts[f, i].edges = sp.atleast_2d(sp.array([sp.ravel_multi_index([k[m], l[m]], gg.segmentgraph.seg_edges.shape), 0]))
                    else:
                        if idx.shape[0] > 0:
                            counts[f, i].edges = sp.r_[counts[f, i].edges, sp.atleast_2d(sp.array([sp.ravel_multi_index([k[m], l[m]], gg.segmentgraph.seg_edges.shape), sp.sum(intron_list[idx, 2])]))]
                        else:
                            counts[f, i].edges = sp.r_[counts[f, i].edges, sp.atleast_2d(sp.array([sp.ravel_multi_index([k[m], l[m]], gg.segmentgraph.seg_edges.shape), 0]))]

    if fn_out is not None:
        pickle.dump(counts, open(fn_out, 'wb'), -1)
    else:
        return counts



def count_graph_coverage_wrapper(fname_in, fname_out, CFG, sample_idx=None):

    (genes, inserted) = pickle.load(open(fname_in, 'rb'))
    
    if genes[0].segmentgraph is None or genes[0].segmentgraph.is_empty():
        for g in genes:
            g.segmentgraph = Segmentgraph(g)
        pickle.dump((genes, inserted), open(fname_in, 'wb'), -1)

    counts = dict()
    counts['segments'] = []
    counts['seg_pos'] = []
    counts['gene_ids_segs'] = []
    counts['edges'] = []
    counts['gene_ids_edges'] = []
    counts['seg_len'] = sp.hstack([x.segmentgraph.segments[1, :] - x.segmentgraph.segments[0, :] for x in genes]).T
    counts['gene_names'] = sp.array([x.name for x in genes], dtype='str')

    if not CFG['rproc']:
        if CFG['merge_strategy'] == 'single':
            print('\nprocessing %s' % (CFG['samples'][sample_idx]))
            counts_tmp = count_graph_coverage(genes, CFG['bam_fnames'][sample_idx], CFG)
        else:
            for s_idx in range(CFG['strains'].shape[0]):
                print('\n%i/%i' % (s_idx + 1, CFG['strains'].shape[0]))
                if s_idx == 0:
                    counts_tmp = count_graph_coverage(genes, CFG['bam_fnames'][s_idx], CFG)
                else:
                    counts_tmp = sp.r_[sp.atleast_2d(counts_tmp), count_graph_coverage(genes, CFG['bam_fnames'][s_idx], CFG)]

        for c in range(counts_tmp.shape[1]):
            counts['segments'].append(sp.hstack([sp.atleast_2d(x.segments).T for x in counts_tmp[:, c]]))
            counts['seg_pos'].append(sp.hstack([sp.atleast_2d(x.seg_pos).T for x in counts_tmp[:, c]]))
            counts['gene_ids_segs'].append(sp.ones((sp.atleast_2d(counts_tmp[0, c].seg_pos).shape[1], 1), dtype='int') * c)
            tmp = [sp.atleast_2d(x.edges) for x in counts_tmp[:, c] if x.edges.shape[0] > 0]
            if len(tmp) == 0:
                continue
            tmp = sp.hstack(tmp)
            if tmp.shape[0] > 0:
                counts['edges'].append(sp.c_[tmp[:, 0], tmp[:, list(range(1, tmp.shape[1], 2))]])
                counts['gene_ids_edges'].append(sp.ones((tmp.shape[0], 1), dtype='int') * c)

        ### write result data to hdf5
        for key in counts:
            counts[key] = sp.vstack(counts[key]) if len(counts[key]) > 0 else counts[key]
        counts['edge_idx'] = counts['edges'][:, 0] if len(counts['edges']) > 0 else sp.array([])
        counts['edges'] = counts['edges'][:, 1:] if len(counts['edges']) > 0 else sp.array([])
        h5fid = h5py.File(fname_out, 'w')
        h5fid.create_dataset(name='strains', data=[val.encode('utf8').strip() for val in CFG['strains']])
        for key in counts:
            if key != 'gene_names':
                h5fid.create_dataset(name=key, data=counts[key])
            else:
                h5fid.create_dataset(name=key, data=[val[0].encode('utf8').strip() for val in counts[key]])
        h5fid.close()
    else:
        ### have an adaptive chunk size, that takes into account the number of strains (take as many genes as it takes to have ~10K strains)
        chunksize = int(max(1, math.floor(10000 / len(CFG['strains']))))

        jobinfo = []

        PAR = dict()
        PAR['CFG'] = CFG.copy()
        if CFG['merge_strategy'] == 'single':
            PAR['CFG']['bam_fnames'] = PAR['CFG']['bam_fnames'][sample_idx]
            PAR['CFG']['samples'] = PAR['CFG']['samples'][sample_idx]
            PAR['CFG']['strains'] = PAR['CFG']['strains'][sample_idx]

        #s_idx = sp.argsort([x.chr for x in genes]) # TODO
        s_idx = sp.arange(genes.shape[0])
        for c_idx in range(0, s_idx.shape[0], chunksize):
            cc_idx = min(s_idx.shape[0], c_idx + chunksize)
            fn = re.sub(r'.hdf5$', '', fname_out) + '.chunk_%i_%i.pickle' % (c_idx, cc_idx)
            if os.path.exists(fn):
                continue
            else:
                print('submitting chunk %i to %i (%i)' % (c_idx, cc_idx, s_idx.shape[0]))
                PAR['genes'] = genes[s_idx][c_idx:cc_idx]
                PAR['fn_bam'] = CFG['bam_fnames']
                PAR['fn_out'] = fn
                PAR['CFG'] = CFG
                jobinfo.append(rp.rproc('count_graph_coverage', PAR, 15000, CFG['options_rproc'], 60*12))

        rp.rproc_wait(jobinfo, 30, 1.0, -1)
        del genes

        ### merge results from count chunks
        if 'verbose' in CFG and CFG['verbose']:
            print('\nCollecting count data from chunks ...\n')
            print('writing data to %s' % fname_out)

        ### write data to hdf5 continuously
        h5fid = h5py.File(fname_out, 'w')
        h5fid.create_dataset(name='gene_names', data=counts['gene_names'])
        h5fid.create_dataset(name='seg_len', data=counts['seg_len'])
        h5fid.create_dataset(name='strains', data=CFG['strains'])
        for c_idx in range(0, s_idx.shape[0], chunksize):
            cc_idx = min(s_idx.shape[0], c_idx + chunksize)
            if 'verbose' in CFG and CFG['verbose']:
                print('collecting chunk %i-%i (%i)' % (c_idx, cc_idx, s_idx.shape[0]))
            fn = re.sub(r'.hdf5$', '', fname_out) + '.chunk_%i_%i.pickle' % (c_idx, cc_idx)
            if not os.path.exists(fn):
                print('ERROR: Not all chunks in counting graph coverage completed!', file=sys.stderr)
                sys.exit(1)
            else:
                counts_tmp = pickle.load(open(fn, 'rb'))
                for c in range(counts_tmp.shape[1]):
                    if 'segments' in h5fid:
                        appendToHDF5(h5fid, sp.hstack([sp.atleast_2d(x.segments).T for x in counts_tmp[:, c]]), 'segments')
                        appendToHDF5(h5fid, sp.hstack([sp.atleast_2d(x.seg_pos).T for x in counts_tmp[:, c]]), 'seg_pos') 
                        appendToHDF5(h5fid, sp.ones((sp.atleast_2d(counts_tmp[0, c].seg_pos).shape[1], 1), dtype='int') * (s_idx[c_idx + c]), 'gene_ids_segs')
                    else:
                        h5fid.create_dataset(name='segments', data=sp.hstack([sp.atleast_2d(x.segments).T for x in counts_tmp[:, c]]), chunks=True, compression='gzip', maxshape=(None, len(CFG['strains'])))
                        h5fid.create_dataset(name='seg_pos', data=sp.hstack([sp.atleast_2d(x.seg_pos).T for x in counts_tmp[:, c]]), chunks=True, compression='gzip', maxshape=(None, len(CFG['strains'])))
                        h5fid.create_dataset(name='gene_ids_segs', data=sp.ones((sp.atleast_2d(counts_tmp[0, c].seg_pos).shape[1], 1), dtype='int') * (s_idx[c_idx + c]), chunks=True, compression='gzip', maxshape=(None, 1))

                    tmp = [sp.atleast_2d(x.edges) for x in counts_tmp[:, c] if x.edges.shape[0] > 0]
                    if len(tmp) == 0:
                        continue
                    tmp = sp.hstack(tmp)
                    if tmp.shape[0] > 0:
                        if 'edges' in h5fid:
                            appendToHDF5(h5fid, tmp[:, list(range(1, tmp.shape[1], 2))], 'edges')
                            appendToHDF5(h5fid, tmp[:, 0], 'edge_idx')
                            appendToHDF5(h5fid, sp.ones((tmp.shape[0], 1), dtype='int') * (s_idx[c_idx + c]), 'gene_ids_edges')
                        else:
                            h5fid.create_dataset(name='edges', data=tmp[:, list(range(1, tmp.shape[1], 2))], chunks=True, compression='gzip', maxshape=(None, tmp.shape[1] / 2))
                            h5fid.create_dataset(name='edge_idx', data=tmp[:, 0], chunks=True, compression='gzip', maxshape=(None,))
                            h5fid.create_dataset(name='gene_ids_edges', data=sp.ones((tmp.shape[0], 1), dtype='int') * (s_idx[c_idx + c]), chunks=True, compression='gzip', maxshape=(None, 1))
                del tmp, counts_tmp
        h5fid.close()

